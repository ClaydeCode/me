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
from datetime import datetime

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from github import Github
from github.Issue import Issue

from clayde.claude import is_claude_available
from clayde.config import get_github_client, get_settings, setup_logging
from clayde.github import (
    fetch_issue,
    fetch_issue_comments,
    get_assigned_issues,
    is_blocked,
    issue_ref,
    parse_issue_url,
)
from clayde.safety import get_new_visible_comments, has_visible_content, is_plan_approved
from clayde.state import IssueStatus, get_issue_state, load_state, update_issue_state
from clayde.tasks import implement, plan, review
from clayde.telemetry import get_tracer, init_tracer

log = logging.getLogger("clayde.orchestrator")

_shutdown = False

# Backward compatibility: treat old status names as their new equivalents
_STATUS_COMPAT = {
    "awaiting_approval": IssueStatus.AWAITING_PLAN_APPROVAL,
}


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


def _handle_new_issue(g: Github, issue: Issue, url: str) -> None:
    """Handle a newly assigned issue — check for visible content and blocked state."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_new_issue", attributes={"issue.url": url}) as span:
        owner, repo, number = parse_issue_url(url)

        # Check if issue is blocked by another open issue
        ref = issue_ref(owner, repo, number)
        label = f"{ref}: {issue.title}"
        try:
            if is_blocked(g, owner, repo, number):
                log.info("[%s] Skipping — blocked by another open issue", label)
                span.set_attribute("issue.skipped", True)
                span.set_attribute("issue.skip_reason", "blocked")
                return
        except Exception as e:
            log.warning("[%s] Failed to check blocked status: %s — proceeding", label, e)

        # Check if there is any visible content to work with
        comments = fetch_issue_comments(g, owner, repo, number)
        if not has_visible_content(issue, comments):
            log.info("[%s] Skipping — no visible content (all filtered out)", label)
            span.set_attribute("issue.skipped", True)
            span.set_attribute("issue.skip_reason", "no_visible_content")
            return

        log.info("[%s] New issue — starting preliminary plan phase", label)
        try:
            plan.run_preliminary(url)
            span.set_attribute("issue.failed", False)
        except Exception as e:
            log.error("[%s] ERROR in preliminary plan: %s", label, e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            span.set_attribute("issue.failed", True)
            update_issue_state(url, {"status": IssueStatus.FAILED})


def _handle_awaiting_approval(g: Github, url: str, issue_state: dict, *, phase: str) -> None:
    """Handle awaiting_*_approval states — check for 👍 or new comments.

    Args:
        phase: Either "preliminary" or "thorough".
    """
    comment_id_key = "preliminary_comment_id" if phase == "preliminary" else "plan_comment_id"
    update_phase = phase
    if phase == "preliminary":
        # Small issues skip thorough planning and go straight to implementation.
        size = issue_state.get("size", "large")
        if size == "small":
            next_task = implement.run
            next_task_label = "implement"
        else:
            next_task = plan.run_thorough
            next_task_label = "thorough_plan"
    else:
        next_task = implement.run
        next_task_label = "implement"

    tracer = get_tracer()
    with tracer.start_as_current_span(f"clayde.handle_awaiting_{phase}_approval", attributes={"issue.url": url}) as span:
        owner = issue_state["owner"]
        repo = issue_state["repo"]
        number = issue_state["number"]
        comment_id = issue_state.get(comment_id_key)
        span.set_attribute("issue.number", number)

        if not comment_id:
            log.warning("[%s] No %s — marking as failed", _issue_label(issue_state), comment_id_key)
            update_issue_state(url, {"status": IssueStatus.FAILED})
            return

        # Check for new visible comments first (plan update)
        if _has_new_comments(g, owner, repo, number, issue_state):
            log.info("[%s] New comments — updating %s plan", _issue_label(issue_state), phase)
            try:
                plan.run_update(url, update_phase)
                span.set_attribute("issue.action", "plan_update")
            except Exception as e:
                log.error("[%s] ERROR updating %s plan: %s", _issue_label(issue_state), phase, e)
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
            return

        # Check for approval (👍 on plan comment)
        if is_plan_approved(g, owner, repo, number, comment_id):
            log.info("[%s] %s plan approved — running %s", _issue_label(issue_state), phase.capitalize(), next_task_label)
            try:
                next_task(url)
                span.set_attribute("issue.action", next_task_label)
            except Exception as e:
                log.error("[%s] ERROR in %s: %s", _issue_label(issue_state), next_task_label, e)
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
                update_issue_state(url, {"status": IssueStatus.FAILED})
            return

        log.info("[%s] Awaiting %s approval", _issue_label(issue_state), phase)
        span.set_attribute("issue.skipped", True)
        span.set_attribute("issue.skip_reason", "not_approved")


def _handle_pr_open(g: Github, url: str, issue_state: dict) -> None:
    """Handle pr_open — check for new reviews on the PR."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_pr_open", attributes={"issue.url": url}) as span:
        span.set_attribute("issue.number", issue_state.get("number", 0))
        log.info("[%s] Checking for PR reviews", _issue_label(issue_state))
        try:
            review.run(url)
            span.set_attribute("issue.failed", False)
        except Exception as e:
            log.error("[%s] ERROR in review handling: %s", _issue_label(issue_state), e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            span.set_attribute("issue.failed", True)
            update_issue_state(url, {"status": IssueStatus.FAILED})


def _handle_interrupted(url: str, issue_state: dict) -> None:
    """Handle interrupted issues — retry the interrupted phase."""
    tracer = get_tracer()
    phase = issue_state.get("interrupted_phase")
    with tracer.start_as_current_span("clayde.handle_interrupted", attributes={"issue.url": url, "issue.interrupted_phase": phase or "unknown"}) as span:
        log.info("[%s] Retrying interrupted issue (phase: %s)", _issue_label(issue_state), phase)

        task_map = {
            IssueStatus.PRELIMINARY_PLANNING: plan.run_preliminary,
            IssueStatus.PLANNING: plan.run_thorough,
            IssueStatus.IMPLEMENTING: implement.run,
            IssueStatus.ADDRESSING_REVIEW: review.run,
        }
        task = task_map.get(phase)
        if task is None:
            log.warning("[%s] Unknown interrupted_phase '%s' — skipping", _issue_label(issue_state), phase)
            span.set_attribute("issue.skipped", True)
            span.set_attribute("issue.skip_reason", "unknown_phase")
            return
        try:
            task(url)
            span.set_attribute("issue.failed", False)
        except Exception as e:
            log.error("[%s] ERROR retrying interrupted issue: %s", _issue_label(issue_state), e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            span.set_attribute("issue.failed", True)
            # Stay in interrupted state so we keep retrying.
            update_issue_state(url, {"status": IssueStatus.INTERRUPTED})


def _has_new_comments(g: Github, owner: str, repo: str, number: int, issue_state: dict) -> bool:
    """Return True if there are new visible comments from non-Clayde users."""
    last_seen = issue_state.get("last_seen_comment_id", 0)
    comments = fetch_issue_comments(g, owner, repo, number)
    return bool(get_new_visible_comments(comments, last_seen))


def main():
    settings = get_settings()

    if not settings.enabled:
        sys.exit(0)

    log.info("=== Starting Clayde Tick [%s] ===", datetime.now().strftime("%Y-%m-%d %H:%M"))

    os.environ["GH_TOKEN"] = settings.github_token

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

        # Recover issues stuck in transient states (e.g. from a crash/restart)
        _TRANSIENT_STATES = {
            IssueStatus.PRELIMINARY_PLANNING,
            IssueStatus.PLANNING,
            IssueStatus.IMPLEMENTING,
            IssueStatus.ADDRESSING_REVIEW,
        }
        for url, ist in issues_state.items():
            status = ist.get("status")
            status = _STATUS_COMPAT.get(status, status)
            if status in _TRANSIENT_STATES:
                log.warning(
                    "Recovering stuck %s (was %s → interrupted)",
                    _issue_label(ist),
                    status,
                )
                update_issue_state(
                    url,
                    {"status": IssueStatus.INTERRUPTED, "interrupted_phase": status},
                )

        # Reload state after recovery mutations
        issues_state = load_state().get("issues", {})

        processed = 0
        for issue in assigned:
            url = issue.html_url
            issue_state = issues_state.get(url, {})
            status = issue_state.get("status")

            # Backward compatibility
            status = _STATUS_COMPAT.get(status, status)

            if status == IssueStatus.DONE:
                continue

            processed += 1
            if status is None:
                _handle_new_issue(g, issue, url)
            elif status == IssueStatus.AWAITING_PRELIMINARY_APPROVAL:
                _handle_awaiting_approval(g, url, issue_state, phase="preliminary")
            elif status == IssueStatus.AWAITING_PLAN_APPROVAL:
                _handle_awaiting_approval(g, url, issue_state, phase="thorough")
            elif status == IssueStatus.PR_OPEN:
                _handle_pr_open(g, url, issue_state)
            elif status == IssueStatus.INTERRUPTED:
                _handle_interrupted(url, issue_state)
            elif status == IssueStatus.FAILED:
                log.info("[%s] Skipping failed issue (clear state to retry)", _issue_label(issue_state))

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

    setup_logging()
    interval = get_settings().loop_interval_s
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
