"""Post-merge wrap-up task — runs wrap-up skill in KB context, notifies via ntfy."""

from __future__ import annotations

import logging
import re
from datetime import date

from clayde.claude import InvocationTimeoutError, UsageLimitError, invoke_claude
from clayde.config import get_settings
from clayde.github import parse_issue_url
from clayde.prompts import render_template
from clayde.responses import WrapUpResponse, parse_response
from clayde.state import get_issue_state
from clayde.telemetry import get_tracer
from clayde.webhook.notify import send_ntfy_sync

log = logging.getLogger("clayde.tasks.wrap_up")


def run(issue_url: str) -> None:
    """Run post-merge wrap-up: invoke wrap-up skill in KB context, notify."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.wrap_up") as span:
        settings = get_settings()
        owner, repo, number = parse_issue_url(issue_url)
        issue_state = get_issue_state(issue_url)

        pr_url = issue_state.get("pr_url", "")
        title = (
            issue_state.get("pr_title")
            or issue_state.get("issue_title")
            or "(unknown)"
        )
        branch_name = issue_state.get("branch_name", f"clayde/issue-{number}")

        words = re.sub(r"[^a-z0-9\s]", "", title.lower()).split()[:3]
        title_slug = "-".join(words) if words else "issue"

        span.set_attribute("issue.number", number)
        span.set_attribute("issue.owner", owner)
        span.set_attribute("issue.repo", repo)

        log.info("[%s/%s#%d] Running post-merge wrap-up", owner, repo, number)

        prompt = render_template(
            "wrap_up.j2",
            owner=owner,
            repo=repo,
            number=number,
            title=title,
            pr_url=pr_url,
            branch_name=branch_name,
            issue_url=issue_url,
            kb_path=settings.kb_path,
            today=date.today().isoformat(),
            topic=title_slug,
        )

        ntfy_title = f"Wrapped up: {owner}/{repo}#{number}"
        ntfy_body = title
        success = False

        try:
            result = invoke_claude(prompt, settings.kb_path)
            try:
                parsed = parse_response(result.output, WrapUpResponse)
                ntfy_title = parsed.title
                ntfy_body = parsed.body
                success = parsed.success
            except ValueError as e:
                log.warning(
                    "[%s/%s#%d] Could not parse wrap-up JSON: %s", owner, repo, number, e
                )
                ntfy_body = f"Wrap-up complete for {owner}/{repo}#{number} (no summary)"
                success = True
            span.set_attribute("wrap_up.success", success)
        except (UsageLimitError, InvocationTimeoutError) as e:
            log.warning("[%s/%s#%d] Wrap-up invoke failed: %s", owner, repo, number, e)
            ntfy_title = f"Wrap-up failed: {owner}/{repo}#{number}"
            ntfy_body = str(e)[:300]
        except Exception as e:
            log.error("[%s/%s#%d] Wrap-up unexpected error: %s", owner, repo, number, e)
            ntfy_title = f"Wrap-up error: {owner}/{repo}#{number}"
            ntfy_body = str(e)[:300]

        if settings.ntfy_topic:
            send_ntfy_sync(
                title=ntfy_title,
                body=ntfy_body,
                success=success,
                base_url=settings.ntfy_base_url,
                topic=settings.ntfy_topic,
                timeout_s=settings.ntfy_timeout_s,
            )
