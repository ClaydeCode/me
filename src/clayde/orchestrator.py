"""Clayde orchestrator — manages the issue lifecycle state machine.

  new → planning → awaiting_approval → implementing → done
                                     ↘ interrupted (usage limit) → (retry)

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
from clayde.github import get_assigned_issues
from clayde.safety import is_issue_authorized, is_plan_approved
from clayde.state import load_state, update_issue_state
from clayde.tasks import implement, plan
from clayde.telemetry import get_tracer, init_tracer

log = logging.getLogger("clayde.orchestrator")

_shutdown = False


def _handle_new_issue(g, issue, url: str) -> None:
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url, "issue.handler": "new"}) as span:
        if not is_issue_authorized(issue):
            log.info("Skipping issue %s — not created by or approved by a whitelisted user", url)
            span.set_attribute("issue.skipped", True)
            span.set_attribute("issue.skip_reason", "unauthorized")
            return
        log.info("New issue: %s — starting plan phase", url)
        try:
            plan.run(url)
            span.set_attribute("issue.failed", False)
        except Exception as e:
            log.error("ERROR in plan for %s: %s", url, e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            span.set_attribute("issue.failed", True)
            update_issue_state(url, {"status": "failed"})


def _handle_awaiting_approval(g, url: str, entry: dict) -> None:
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url, "issue.handler": "awaiting_approval"}) as span:
        owner = entry["owner"]
        repo = entry["repo"]
        number = entry["number"]
        comment_id = entry["plan_comment_id"]
        span.set_attribute("issue.number", number)
        span.set_attribute("issue.owner", owner)
        span.set_attribute("issue.repo", repo)
        if not is_plan_approved(g, owner, repo, number, comment_id):
            log.info("Issue %s awaiting approval", url)
            span.set_attribute("issue.skipped", True)
            span.set_attribute("issue.skip_reason", "not_approved")
            return
        log.info("Plan approved: %s — starting implementation", url)
        try:
            implement.run(url)
            span.set_attribute("issue.failed", False)
        except Exception as e:
            log.error("ERROR in implement for %s: %s", url, e)
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            span.set_attribute("issue.failed", True)
            update_issue_state(url, {"status": "failed"})


def _handle_interrupted(url: str, entry: dict) -> None:
    tracer = get_tracer()
    phase = entry.get("interrupted_phase")
    with tracer.start_as_current_span("clayde.handle_issue", attributes={"issue.url": url, "issue.handler": "interrupted", "issue.interrupted_phase": phase or "unknown"}) as span:
        log.info("Retrying interrupted issue %s (phase: %s)", url, phase)
        task = {"planning": plan.run, "implementing": implement.run}.get(phase)
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

            if status in ("done", "planning", "implementing"):
                continue

            processed += 1
            if status is None:
                _handle_new_issue(g, issue, url)
            elif status == "awaiting_approval":
                _handle_awaiting_approval(g, url, entry)
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
