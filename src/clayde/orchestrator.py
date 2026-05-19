"""Clayde orchestrator — event-driven issue processing loop.

For each assigned issue, Clayde checks whether there has been new
whitelist-visible activity since the last cycle. If so (or if a previous
invocation was interrupted), it calls the unified work task, which lets
Claude decide the next action — ask questions, post a plan, implement, or
address reviews.

Entry points:
  main()      — single cycle (one-shot mode, used for testing/debugging)
  run_loop()  — continuous loop with configurable sleep interval (container mode)
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import uvicorn
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from github import Github
from github.Issue import Issue

from clayde.claude import InvocationTimeoutError, UsageLimitError, is_claude_available
from clayde.config import get_github_client, get_settings, setup_logging
from clayde.webhook import JobQueue, create_app, worker_loop
from clayde.github import (
    fetch_issue,
    fetch_issue_comments,
    get_assigned_issues,
    get_pr_review_comments,
    get_pr_reviews,
    is_blocked,
    issue_ref,
    parse_issue_url,
    parse_pr_url,
)
from clayde.safety import get_new_visible_comments, has_visible_content
from clayde.state import get_issue_state, load_state, save_state, update_issue_state
from clayde.tasks import work
from clayde.telemetry import get_tracer, init_tracer

log = logging.getLogger("clayde.orchestrator")

_shutdown = False


def _issue_label(issue_state: dict) -> str:
    """Return 'owner/repo#N: title' for display in log lines."""
    owner = issue_state.get("owner", "?")
    repo = issue_state.get("repo", "?")
    number = issue_state.get("number", "?")
    title = issue_state.get("pr_title") or issue_state.get("issue_title")
    ref = issue_ref(owner, repo, number)
    if title:
        return f"{ref}: {title}"
    return f"{ref} (title unknown)"


def _parse_timestamp(ts: str | None) -> datetime | None:
    """Parse an ISO UTC timestamp string into an aware datetime, or return None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _now_utc() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _handle_issue(g: Github, issue: Issue, url: str) -> None:
    """Check for new activity and invoke Claude if needed."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url}) as span:
        owner, repo, number = parse_issue_url(url)
        ref = issue_ref(owner, repo, number)
        label = f"{ref}: {issue.title}"

        # Skip blocked issues
        try:
            if is_blocked(g, owner, repo, number):
                log.info("[%s] Skipping — blocked by another open issue", label)
                span.set_attribute("issue.skip_reason", "blocked")
                return
        except Exception as e:
            log.warning("[%s] Failed to check blocked status: %s — proceeding", label, e)

        # Skip issues with no whitelist-visible content
        comments = fetch_issue_comments(g, owner, repo, number)
        if not has_visible_content(issue, comments):
            log.info("[%s] Skipping — no visible content (all filtered out)", label)
            span.set_attribute("issue.skip_reason", "no_visible_content")
            return

        issue_state = get_issue_state(url)
        in_progress = issue_state.get("in_progress", False)
        last_seen_at = _parse_timestamp(issue_state.get("last_seen_at"))

        # Check for new visible comments since last cycle
        new_comments = get_new_visible_comments(comments, last_seen_at)

        # Check for new PR review activity
        has_new_review_activity = False
        pr_url = issue_state.get("pr_url")
        if pr_url and last_seen_at is not None:
            try:
                _, _, pr_number = parse_pr_url(pr_url)
                reviews = get_pr_reviews(g, owner, repo, pr_number)
                review_comments = get_pr_review_comments(g, owner, repo, pr_number)
                github_username = get_settings().github_username
                new_reviews = [
                    r for r in reviews
                    if r.submitted_at > last_seen_at
                    and r.user.login != github_username
                ]
                if new_reviews:
                    new_review_ids = {r.id for r in new_reviews}
                    has_inline = any(
                        rc.pull_request_review_id in new_review_ids
                        for rc in review_comments
                    )
                    has_bodies = any(r.body and r.body.strip() for r in new_reviews)
                    if has_inline or has_bodies:
                        has_new_review_activity = True
                    else:
                        # Pure approval (no comments) — update timestamp without invoking Claude
                        log.info("[%s] Pure PR approval — updating last_seen_at", label)
                        update_issue_state(url, {"last_seen_at": _now_utc()})
                        span.set_attribute("issue.skip_reason", "pure_approval")
                        return
            except Exception as e:
                log.warning("[%s] Failed to check PR reviews: %s", label, e)

        should_invoke = in_progress or (last_seen_at is None) or bool(new_comments) or has_new_review_activity

        if not should_invoke:
            log.info("[%s] No new activity — skipping", label)
            span.set_attribute("issue.skip_reason", "no_new_activity")
            return

        # Mark in_progress before invoking Claude so a crash leaves a retry marker
        update_issue_state(url, {"in_progress": True})

        log.info("[%s] New activity — invoking work task", label)
        try:
            work.run(url)
        except (UsageLimitError, InvocationTimeoutError) as e:
            log.warning("[%s] Usage/timeout limit — will retry next cycle: %s", label, e)
            span.set_attribute("issue.status", "retry")
            # in_progress stays True so the next cycle retries automatically
            return
        except Exception as e:
            log.error("[%s] ERROR in work task: %s", label, e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            update_issue_state(url, {"in_progress": False})
            return

        # Successful completion — update last_seen_at to prevent re-triggering on
        # Clayde's own comments posted during this run
        update_issue_state(url, {"in_progress": False, "last_seen_at": _now_utc()})
        span.set_attribute("issue.status", "completed")
        log.info("[%s] Cycle complete", label)


def _prune_closed_issues(g: Github, issues_state: dict) -> None:
    """Remove closed issues from state to prevent stale entries accumulating."""
    to_prune = []
    for url, ist in issues_state.items():
        owner = ist.get("owner")
        repo = ist.get("repo")
        number = ist.get("number")
        if not owner or not repo or not number:
            continue
        try:
            issue = fetch_issue(g, owner, repo, number)
            if issue.state == "closed":
                to_prune.append(url)
        except Exception as e:
            log.warning("[%s] Failed to check issue state for pruning: %s — skipping", _issue_label(ist), e)

    if to_prune:
        state = load_state()
        for url in to_prune:
            ist = issues_state[url]
            log.info("[%s] Pruning closed issue from state", _issue_label(ist))
            state["issues"].pop(url, None)
        save_state(state)


def main():
    settings = get_settings()

    if not settings.enabled:
        sys.exit(0)

    log.info("=== Starting Clayde Tick [%s] ===", datetime.now().strftime("%Y-%m-%d %H:%M"))

    os.environ["GH_TOKEN"] = settings.github_token

    git_name = settings.effective_git_name
    git_email = settings.git_email
    if not git_name or not git_email:
        log.error("CLAYDE_GIT_NAME (or CLAYDE_GITHUB_USERNAME) and CLAYDE_GIT_EMAIL must be set")
        sys.exit(1)
    subprocess.run(["git", "config", "--global", "user.name", git_name], check=True)
    subprocess.run(["git", "config", "--global", "user.email", git_email], check=True)

    provider = init_tracer()
    tracer = get_tracer()

    with tracer.start_as_current_span("clayde.tick") as tick_span:
        if not is_claude_available():
            log.warning("Claude usage limit hit — skipping all work this cycle")
            tick_span.set_attribute("claude.available", False)
            provider.force_flush()
            return

        tick_span.set_attribute("claude.available", True)
        g = get_github_client()
        assigned = get_assigned_issues(g)

        tick_span.set_attribute("issues.assigned_count", len(assigned))

        # Prune closed issues from state before any other processing
        issues_state = load_state().get("issues", {})
        _prune_closed_issues(g, issues_state)

        # Reload state after pruning
        issues_state = load_state().get("issues", {})

        if not assigned:
            log.info("No assigned issues. Going back to sleep.")
            provider.force_flush()
            return

        processed = 0
        for issue in assigned:
            url = issue.html_url
            processed += 1
            _handle_issue(g, issue, url)

        tick_span.set_attribute("issues.processed", processed)

    provider.force_flush()


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("Received signal %s — will shut down after current cycle", signum)


async def _run_with_pebble() -> None:
    """Async entry point that runs the GitHub tick loop, the Pebble webhook,
    and the Pebble worker concurrently.
    """
    setup_logging()
    settings = get_settings()
    interval = settings.loop_interval_s
    log.info(
        "Starting Clayde with Pebble webhook (port=%d, queue_max=%d)",
        settings.pebble_port, settings.pebble_queue_max,
    )

    queue = JobQueue(maxsize=settings.pebble_queue_max)
    app = create_app(queue=queue, expected_token=settings.pebble_token)
    config = uvicorn.Config(
        app, host="0.0.0.0", port=settings.pebble_port,
        log_level="info", access_log=True, lifespan="off",
    )
    server = uvicorn.Server(config)

    async def tick_loop() -> None:
        while not _shutdown:
            try:
                await asyncio.to_thread(main)
            except SystemExit:
                pass
            except Exception:
                log.exception("Unhandled error in main loop")
            for _ in range(interval):
                if _shutdown:
                    break
                await asyncio.sleep(1)

    async def worker_task() -> None:
        await worker_loop(
            queue,
            timeout_s=settings.pebble_timeout,
            kb_path=settings.kb_path,
        )

    await asyncio.gather(server.serve(), tick_loop(), worker_task())


def run_loop():
    """Run main() in a loop with a configurable sleep interval.

    This is the container entry point. When ``pebble_enabled`` is true,
    also serves the Pebble webhook + worker on the same event loop.
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    settings = get_settings()
    if settings.pebble_enabled:
        asyncio.run(_run_with_pebble())
        return

    setup_logging()
    interval = settings.loop_interval_s
    log.info("Starting Clayde loop (interval=%ds)", interval)

    while not _shutdown:
        try:
            main()
        except SystemExit:
            pass  # main() calls sys.exit(0) when disabled
        except Exception:
            log.exception("Unhandled error in main loop")
        if not _shutdown:
            time.sleep(interval)
