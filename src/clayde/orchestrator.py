"""Clayde orchestrator — manages the issue lifecycle state machine.

  new → preliminary_planning → awaiting_preliminary_approval
      → planning → awaiting_plan_approval → implementing → pr_open → done
                                                                   ↘ failed

New comment detection triggers plan updates in awaiting_* states.
PR reviews are handled in pr_open state.

Entry points:
  main()      — single cycle (one-shot mode, used for testing/debugging)
  run_loop()  — continuous loop with configurable sleep interval (container mode)
"""

import logging
import os
import signal
import sys
import time

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from clayde.claude import is_claude_available
from clayde.config import get_github_client, get_settings, setup_logging
from clayde.github import (
    fetch_issue,
    fetch_issue_comments,
    get_assigned_issues,
    is_blocked,
    parse_issue_url,
)
from clayde.safety import filter_comments, has_visible_content, is_plan_approved
from clayde.state import get_issue_state, load_state, update_issue_state
from clayde.tasks import implement, plan, review
from clayde.telemetry import get_tracer, init_tracer

log = logging.getLogger("clayde.orchestrator")

_shutdown = False

# Backward compatibility: treat old status names as their new equivalents
_STATUS_COMPAT = {
    "awaiting_approval": "awaiting_plan_approval",
}


def _handle_new_issue(g, issue, url: str) -> None:
    """Handle a newly assigned issue — check for visible content and blocked state."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url, "issue.handler": "new"}) as span:
        owner, repo, number = parse_issue_url(url)

        # Check if issue is blocked by another open issue
        try:
            if is_blocked(g, owner, repo, number):
                log.info("Skipping issue %s — blocked by another open issue", url)
                span.set_attribute("issue.skipped", True)
                span.set_attribute("issue.skip_reason", "blocked")
                return
        except Exception as e:
            log.warning("Failed to check blocked status for %s: %s — proceeding", url, e)

        # Check if there is any visible content to work with
        comments = fetch_issue_comments(g, owner, repo, number)
        if not has_visible_content(issue, comments):
            log.info("Skipping issue %s — no visible content (all filtered out)", url)
            span.set_attribute("issue.skipped", True)
            span.set_attribute("issue.skip_reason", "no_visible_content")
            return

        log.info("New issue: %s — starting preliminary plan phase", url)
        try:
            plan.run_preliminary(url)
            span.set_attribute("issue.failed", False)
        except Exception as e:
            log.error("ERROR in preliminary plan for %s: %s", url, e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            span.set_attribute("issue.failed", True)
            update_issue_state(url, {"status": "failed"})


def _handle_awaiting_preliminary(g, url: str, entry: dict) -> None:
    """Handle awaiting_preliminary_approval — check for 👍 or new comments."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url, "issue.handler": "awaiting_preliminary_approval"}) as span:
        owner = entry["owner"]
        repo = entry["repo"]
        number = entry["number"]
        comment_id = entry.get("preliminary_comment_id")
        span.set_attribute("issue.number", number)

        if not comment_id:
            log.warning("No preliminary_comment_id for %s — marking as failed", url)
            update_issue_state(url, {"status": "failed"})
            return

        # Check for new visible comments first (plan update)
        if _has_new_comments(g, owner, repo, number, entry):
            log.info("New comments on %s — updating preliminary plan", url)
            try:
                plan.run_update(url, "preliminary")
                span.set_attribute("issue.action", "plan_update")
            except Exception as e:
                log.error("ERROR updating preliminary plan for %s: %s", url, e)
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
            return

        # Check for approval (👍 on preliminary plan comment)
        if is_plan_approved(g, owner, repo, number, comment_id):
            log.info("Preliminary plan approved: %s — starting thorough plan", url)
            try:
                plan.run_thorough(url)
                span.set_attribute("issue.action", "thorough_plan")
            except Exception as e:
                log.error("ERROR in thorough plan for %s: %s", url, e)
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
                update_issue_state(url, {"status": "failed"})
            return

        log.info("Issue %s awaiting preliminary approval", url)
        span.set_attribute("issue.skipped", True)
        span.set_attribute("issue.skip_reason", "not_approved")


def _handle_awaiting_plan(g, url: str, entry: dict) -> None:
    """Handle awaiting_plan_approval — check for 👍 or new comments."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url, "issue.handler": "awaiting_plan_approval"}) as span:
        owner = entry["owner"]
        repo = entry["repo"]
        number = entry["number"]
        comment_id = entry.get("plan_comment_id")
        span.set_attribute("issue.number", number)

        if not comment_id:
            log.warning("No plan_comment_id for %s — marking as failed", url)
            update_issue_state(url, {"status": "failed"})
            return

        # Check for new visible comments first (plan update)
        if _has_new_comments(g, owner, repo, number, entry):
            log.info("New comments on %s — updating thorough plan", url)
            try:
                plan.run_update(url, "thorough")
                span.set_attribute("issue.action", "plan_update")
            except Exception as e:
                log.error("ERROR updating thorough plan for %s: %s", url, e)
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
            return

        # Check for approval (👍 on thorough plan comment)
        if is_plan_approved(g, owner, repo, number, comment_id):
            log.info("Plan approved: %s — starting implementation", url)
            try:
                implement.run(url)
                span.set_attribute("issue.action", "implement")
            except Exception as e:
                log.error("ERROR in implement for %s: %s", url, e)
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
                update_issue_state(url, {"status": "failed"})
            return

        log.info("Issue %s awaiting plan approval", url)
        span.set_attribute("issue.skipped", True)
        span.set_attribute("issue.skip_reason", "not_approved")


def _handle_pr_open(g, url: str, entry: dict) -> None:
    """Handle pr_open — check for new reviews on the PR."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url, "issue.handler": "pr_open"}) as span:
        span.set_attribute("issue.number", entry.get("number", 0))
        log.info("Checking for PR reviews on %s", url)
        try:
            review.run(url)
            span.set_attribute("issue.failed", False)
        except Exception as e:
            log.error("ERROR in review handling for %s: %s", url, e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            span.set_attribute("issue.failed", True)
            update_issue_state(url, {"status": "failed"})


def _handle_interrupted(url: str, entry: dict) -> None:
    """Handle interrupted issues — retry the interrupted phase."""
    tracer = get_tracer()
    phase = entry.get("interrupted_phase")
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url, "issue.handler": "interrupted", "issue.interrupted_phase": phase or "unknown"}) as span:
        log.info("Retrying interrupted issue %s (phase: %s)", url, phase)

        task_map = {
            "preliminary_planning": plan.run_preliminary,
            "planning": plan.run_thorough,
            "implementing": implement.run,
            "addressing_review": review.run,
        }
        task = task_map.get(phase)
        if task is None:
            log.warning("Unknown interrupted_phase '%s' for %s — skipping", phase, url)
            span.set_attribute("issue.skipped", True)
            span.set_attribute("issue.skip_reason", "unknown_phase")
            return
        try:
            task(url)
            span.set_attribute("issue.failed", False)
        except Exception as e:
            log.error("ERROR retrying interrupted issue %s: %s", url, e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            span.set_attribute("issue.failed", True)
            # Stay in interrupted state so we keep retrying.
            update_issue_state(url, {"status": "interrupted"})


def _has_new_comments(g, owner: str, repo: str, number: int, entry: dict) -> bool:
    """Check if there are new visible comments from non-Clayde users."""
    last_seen = entry.get("last_seen_comment_id", 0)
    github_username = get_settings().github_username

    comments = fetch_issue_comments(g, owner, repo, number)
    visible = filter_comments(comments)
    new_visible = [
        c for c in visible
        if c.id > last_seen and c.user.login != github_username
    ]
    return len(new_visible) > 0


def main():
    settings = get_settings()

    if not settings.enabled:
        sys.exit(0)

    os.environ["GH_TOKEN"] = settings.github_token

    setup_logging()
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
        state = load_state()
        issues_state = state.get("issues", {})

        tick_span.set_attribute("issues.assigned_count", len(assigned))

        if not assigned:
            log.info("No assigned issues. Going back to sleep.")
            provider.force_flush()
            return

        processed = 0
        for issue in assigned:
            url = issue.html_url
            entry = issues_state.get(url, {})
            status = entry.get("status")

            # Backward compatibility
            status = _STATUS_COMPAT.get(status, status)

            # Skip in-progress or completed states
            if status in ("done", "preliminary_planning", "planning",
                          "implementing", "addressing_review"):
                continue

            processed += 1
            if status is None:
                _handle_new_issue(g, issue, url)
            elif status == "awaiting_preliminary_approval":
                _handle_awaiting_preliminary(g, url, entry)
            elif status == "awaiting_plan_approval":
                _handle_awaiting_plan(g, url, entry)
            elif status == "pr_open":
                _handle_pr_open(g, url, entry)
            elif status == "interrupted":
                _handle_interrupted(url, entry)
            elif status == "failed":
                log.info("Skipping failed issue: %s (clear state to retry)", url)

        tick_span.set_attribute("issues.processed", processed)

    provider.force_flush()


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("Received signal %s — will shut down after current cycle", signum)


def run_loop():
    """Run main() in a loop with a configurable sleep interval.

    This is the container entry point. Handles SIGTERM/SIGINT for graceful
    shutdown and guarantees no overlapping work sessions.
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    interval = int(os.environ.get("CLAYDE_INTERVAL", "300"))

    setup_logging()
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
