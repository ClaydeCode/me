"""Clayde orchestrator — entry point called by cron every 15 minutes.

Manages the issue lifecycle as a state machine:
  new → planning → awaiting_approval → implementing → done
                                     ↘ interrupted (usage limit) → (retry)
"""

import logging
import os
import sys

from clayde.claude import is_claude_available
from clayde.config import get_github_client, get_settings, setup_logging
from clayde.github import get_assigned_issues
from clayde.safety import is_issue_authorized, is_plan_approved
from clayde.state import load_state, update_issue_state
from clayde.tasks import implement, plan

log = logging.getLogger("clayde.orchestrator")


def _handle_new_issue(g, issue, url: str) -> None:
    if not is_issue_authorized(issue):
        log.info("Skipping issue %s — not created by or approved by a whitelisted user", url)
        return
    log.info("New issue: %s — starting plan phase", url)
    try:
        plan.run(url)
    except Exception as e:
        log.error("ERROR in plan for %s: %s", url, e)
        update_issue_state(url, {"status": "failed"})


def _handle_awaiting_approval(g, url: str, entry: dict) -> None:
    owner = entry["owner"]
    repo = entry["repo"]
    number = entry["number"]
    comment_id = entry["plan_comment_id"]
    if not is_plan_approved(g, owner, repo, number, comment_id):
        log.info("Issue %s awaiting approval", url)
        return
    log.info("Plan approved: %s — starting implementation", url)
    try:
        implement.run(url)
    except Exception as e:
        log.error("ERROR in implement for %s: %s", url, e)
        update_issue_state(url, {"status": "failed"})


def _handle_interrupted(url: str, entry: dict) -> None:
    phase = entry.get("interrupted_phase")
    log.info("Retrying interrupted issue %s (phase: %s)", url, phase)
    task = {"planning": plan.run, "implementing": implement.run}.get(phase)
    if task is None:
        log.warning("Unknown interrupted_phase '%s' for %s — skipping", phase, url)
        return
    try:
        task(url)
    except Exception as e:
        log.error("ERROR retrying interrupted issue %s: %s", url, e)
        # Stay in interrupted state so we keep retrying.
        update_issue_state(url, {"status": "interrupted"})


def main():
    settings = get_settings()

    if not settings.enabled:
        sys.exit(0)

    os.environ["GH_TOKEN"] = settings.github_token

    setup_logging()

    if not is_claude_available():
        log.warning("Claude usage limit hit — skipping all work this cycle")
        return

    g = get_github_client()
    assigned = get_assigned_issues(g)
    state = load_state()
    issues_state = state.get("issues", {})

    if not assigned:
        log.info("No assigned issues. Going back to sleep.")
        return

    for issue in assigned:
        url = issue.html_url
        entry = issues_state.get(url, {})
        status = entry.get("status")

        if status in ("done", "planning", "implementing"):
            continue

        if status is None:
            _handle_new_issue(g, issue, url)
        elif status == "awaiting_approval":
            _handle_awaiting_approval(g, url, entry)
        elif status == "interrupted":
            _handle_interrupted(url, entry)
        elif status == "failed":
            log.info("Skipping failed issue: %s (clear state to retry)", url)
