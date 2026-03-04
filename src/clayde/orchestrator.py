"""Clayde orchestrator — entry point called by cron every 15 minutes.

Manages the issue lifecycle as a state machine:
  new → planning → awaiting_approval → implementing → done
                                     ↘ interrupted (usage limit) → (retry)
"""

import logging
import os
import sys

from clayde.claude import is_claude_available
from clayde.config import load_config, setup_logging
from clayde.github import (
    check_approval,
    get_assigned_issues,
    has_whitelisted_thumbsup,
    is_whitelisted_author,
)
from clayde.implementer import do_implement
from clayde.planner import do_plan
from clayde.state import load_state

log = logging.getLogger("clayde.orchestrator")


def main():
    config = load_config()

    if config.get("CLAYDE_ENABLED", "false").lower() != "true":
        sys.exit(0)

    os.environ["GH_TOKEN"] = config["GITHUB_TOKEN"]

    setup_logging()

    if not is_claude_available():
        log.warning("Claude usage limit hit — skipping all work this cycle")
        return

    assigned = get_assigned_issues()
    state = load_state()
    issues_state = state.get("issues", {})

    if not assigned:
        log.info("No assigned issues. Going back to sleep.")
        return

    for issue in assigned:
        url = issue["html_url"]
        entry = issues_state.get(url, {})
        status = entry.get("status")

        if status in ("done", "planning", "implementing"):
            continue

        if status is None:
            # Safety gate: only plan if a whitelisted user created the
            # issue or gave it a +1 reaction.
            owner_repo = issue["repository"]["full_name"].split("/")
            i_owner, i_repo = owner_repo[0], owner_repo[1]
            i_number = issue["number"]
            if not is_whitelisted_author(issue) and not has_whitelisted_thumbsup(
                i_owner, i_repo, i_number
            ):
                log.info(
                    "Skipping issue %s — not created by or approved by a whitelisted user",
                    url,
                )
                continue

            log.info("New issue: %s — starting plan phase", url)
            try:
                do_plan(url)
            except Exception as e:
                log.error("ERROR in plan for %s: %s", url, e)
                from clayde.state import update_issue_state
                update_issue_state(url, {"status": "failed"})

        elif status == "awaiting_approval":
            owner = entry["owner"]
            repo = entry["repo"]
            number = entry["number"]
            comment_id = entry["plan_comment_id"]
            # Safety gate: implementation requires +1 from a whitelisted
            # user on the plan comment (existing check) AND on the issue.
            if not has_whitelisted_thumbsup(owner, repo, number):
                log.info(
                    "Issue %s awaiting +1 from whitelisted user before implementation",
                    url,
                )
                continue
            if check_approval(owner, repo, comment_id):
                log.info("Plan approved: %s — starting implementation", url)
                try:
                    do_implement(url)
                except Exception as e:
                    log.error("ERROR in implement for %s: %s", url, e)
                    from clayde.state import update_issue_state
                    update_issue_state(url, {"status": "failed"})

        elif status == "interrupted":
            phase = entry.get("interrupted_phase")
            log.info("Retrying interrupted issue %s (phase: %s)", url, phase)
            try:
                if phase == "planning":
                    do_plan(url)
                elif phase == "implementing":
                    do_implement(url)
                else:
                    log.warning("Unknown interrupted_phase '%s' for %s — skipping", phase, url)
            except Exception as e:
                log.error("ERROR retrying interrupted issue %s: %s", url, e)
                from clayde.state import update_issue_state
                # Stay in interrupted state so we keep retrying —
                # the limit may persist for hours until usage resets.
                update_issue_state(url, {"status": "interrupted"})

        elif status == "failed":
            log.info("Skipping failed issue: %s (clear state to retry)", url)

