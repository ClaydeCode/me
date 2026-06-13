"""CI-fix task — diagnose a failing pipeline on a clayde-opened PR and push a fix.

Invoked by the orchestrator when a required check on an open PR has failed and a
fix has not yet been attempted for that head commit.  Claude inspects the
failing job logs, pushes a fix commit to the PR branch, and a summary is posted
as an issue comment.  Mirrors the structure of ``tasks/work.py``.
"""

import logging

from clayde.claude import format_cost_line, invoke_claude
from clayde.config import get_github_client
from clayde.git import ensure_repo
from clayde.github import (
    fetch_issue,
    get_default_branch,
    issue_ref,
    parse_issue_url,
    post_comment,
)
from clayde.prompts import render_template
from clayde.responses import WorkResponse, parse_response
from clayde.safety import is_issue_visible
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.tasks.fix_ci")


def _format_failed_checks(failed_checks: list[dict]) -> str:
    """Render failed check runs into a readable list for the prompt."""
    lines = []
    for check in failed_checks:
        name = check.get("name", "(unknown)")
        conclusion = check.get("conclusion", "failure")
        url = check.get("details_url", "")
        line = f"- {name} ({conclusion})"
        if url:
            line += f" — {url}"
        lines.append(line)
    return "\n".join(lines) or "(none)"


def run(issue_url: str, pr_url: str, branch_name: str, failed_checks: list[dict]) -> None:
    """Invoke Claude to fix a failing CI pipeline on the PR branch.

    Raises UsageLimitError or InvocationTimeoutError on rate/timeout limits so
    the orchestrator can react (the same head SHA will be retried next cycle).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.fix_ci") as span:
        g = get_github_client()
        owner, repo, number = parse_issue_url(issue_url)
        ref = issue_ref(owner, repo, number)
        span.set_attribute("issue.number", number)
        span.set_attribute("issue.owner", owner)
        span.set_attribute("issue.repo", repo)
        span.set_attribute("ci_fix.failed_count", len(failed_checks))

        issue = fetch_issue(g, owner, repo, number)
        default_branch = get_default_branch(g, owner, repo)
        repo_path = ensure_repo(owner, repo, default_branch)

        body_text = issue.body or "(empty)"
        if not is_issue_visible(issue):
            body_text = "(filtered)"
        labels = ", ".join(lb.name for lb in issue.labels) or "none"

        prompt = render_template(
            "fix_ci.j2",
            number=number,
            title=issue.title,
            owner=owner,
            repo=repo,
            labels=labels,
            body=body_text,
            branch_name=branch_name,
            pr_url=pr_url,
            failed_checks=_format_failed_checks(failed_checks),
            repo_path=repo_path,
            default_branch=default_branch,
        )

        log.info("[%s: %s] Invoking Claude to fix failing CI", ref, issue.title)

        # UsageLimitError/InvocationTimeoutError propagate to the orchestrator
        result = invoke_claude(prompt, repo_path)

        span.set_attribute("fix_ci.output_length", len(result.output or ""))

        # Parse summary (best-effort; fall back to raw output snippet)
        summary = None
        try:
            parsed = parse_response(result.output, WorkResponse)
            summary = parsed.summary
        except ValueError:
            log.warning("[%s: %s] Failed to parse CI-fix response JSON — using raw output",
                        ref, issue.title)
            summary = (result.output or "").strip()[:500] or None

        if summary:
            check_names = ", ".join(c.get("name", "?") for c in failed_checks)
            header = f"🔧 CI was failing ({check_names}). "
            post_comment(g, owner, repo, number,
                         f"{header}{summary}{format_cost_line(result.cost_eur)}")

        log.info("[%s: %s] CI-fix complete", ref, issue.title)
