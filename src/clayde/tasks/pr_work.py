"""PR work task — address review comments on a standalone assigned PR.

A "standalone PR" is a pull request assigned to Clayde that has no originating
issue in the plan → implement → PR lifecycle.  State is keyed by the PR URL
rather than an issue URL.
"""

import logging

from clayde.claude import format_cost_line, invoke_claude
from clayde.config import get_github_client, get_settings
from clayde.git import ensure_repo
from clayde.github import (
    get_default_branch,
    get_pr_review_comments,
    get_pr_reviews,
    issue_ref,
    parse_pr_url,
    post_comment,
)
from clayde.prompts import render_template
from clayde.responses import WorkResponse, parse_response
from clayde.safety import filter_pr_reviews
from clayde.state import get_issue_state, update_issue_state
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.tasks.pr_work")


def _format_reviews(reviews: list, review_comments: list) -> str:
    """Format PR reviews and inline comments into text for the prompt."""
    parts = []
    for review in reviews:
        header = f"Review by @{review.user.login} (state: {review.state}):"
        inline = [rc for rc in review_comments if rc.pull_request_review_id == review.id]
        has_body = review.body and review.body.strip()
        if has_body or inline:
            parts.append(f"{header}\n{review.body}" if has_body else header)
        for rc in inline:
            file_info = f"  File: {rc.path}"
            if hasattr(rc, "line") and rc.line:
                file_info += f", line {rc.line}"
            parts.append(f"{file_info}\n  {rc.body}")
    return "\n---\n".join(parts) or "(none)"


def run(pr_url: str) -> None:
    """Fetch PR context and invoke Claude to address review comments.

    State is keyed by *pr_url* (not an issue URL).  Raises UsageLimitError or
    InvocationTimeoutError on rate/timeout limits so the orchestrator can
    leave in_progress=True for automatic retry.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.pr_work") as span:
        g = get_github_client()
        owner, repo, pr_number = parse_pr_url(pr_url)
        ref = issue_ref(owner, repo, pr_number)
        span.set_attribute("pr.number", pr_number)
        span.set_attribute("pr.owner", owner)
        span.set_attribute("pr.repo", repo)

        repo_obj = g.get_repo(f"{owner}/{repo}")
        pr = repo_obj.get_pull(pr_number)
        title = pr.title
        body = pr.body or "(empty)"
        branch_name = pr.head.ref
        default_branch = get_default_branch(g, owner, repo)

        # Fetch and whitelist-filter reviews
        settings = get_settings()
        github_username = settings.github_username
        reviews = get_pr_reviews(g, owner, repo, pr_number)
        review_comments = get_pr_review_comments(g, owner, repo, pr_number)
        visible_reviews = filter_pr_reviews(reviews, github_username)
        review_text = _format_reviews(visible_reviews, review_comments)

        # Persist metadata before invoking Claude
        update_issue_state(pr_url, {
            "owner": owner,
            "repo": repo,
            "number": pr_number,
            "pr_title": title,
            "branch_name": branch_name,
            "is_standalone_pr": True,
        })

        repo_path = ensure_repo(owner, repo, default_branch)

        prompt = render_template(
            "pr_work.j2",
            number=pr_number,
            title=title,
            owner=owner,
            repo=repo,
            body=body,
            review_text=review_text,
            repo_path=repo_path,
            branch_name=branch_name,
            pr_url=pr_url,
            default_branch=default_branch,
        )

        log.info("[%s: %s] Invoking Claude for PR review", ref, title)

        # UsageLimitError/InvocationTimeoutError propagate to the orchestrator
        result = invoke_claude(prompt, repo_path)

        span.set_attribute("pr_work.output_length", len(result.output or ""))

        # Parse summary (best-effort; fall back to raw output snippet)
        summary = None
        try:
            parsed = parse_response(result.output, WorkResponse)
            summary = parsed.summary
        except ValueError:
            log.warning("[%s: %s] Failed to parse PR work response JSON — using raw output",
                        ref, title)
            summary = (result.output or "").strip()[:500] or None

        if summary:
            post_comment(g, owner, repo, pr_number,
                         f"{summary}{format_cost_line(result.cost_eur)}")

        log.info("[%s: %s] PR work complete", ref, title)
