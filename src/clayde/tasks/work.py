"""Work task — unified event-driven work on a GitHub issue.

Claude decides what to do next: ask questions, plan, implement, or address
reviews.  PR creation is handled by Claude via `gh pr create`; Python detects
the resulting PR via find_open_pr() and persists it in state.
"""

import logging

from clayde.claude import format_cost_line, invoke_claude
from clayde.config import get_github_client, get_settings
from clayde.git import ensure_repo
from clayde.github import (
    add_pr_reviewer,
    fetch_issue,
    fetch_issue_comments,
    find_open_pr,
    get_default_branch,
    get_issue_author,
    get_pr_review_comments,
    get_pr_reviews,
    issue_ref,
    parse_issue_url,
    parse_pr_url,
    post_comment,
)
from clayde.prompts import render_template
from clayde.responses import WorkResponse, parse_response
from clayde.safety import filter_comments, is_issue_visible
from clayde.state import get_issue_state, update_issue_state
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.tasks.work")


def run(issue_url: str) -> None:
    """Gather full issue context and invoke Claude to take the next action.

    Raises UsageLimitError or InvocationTimeoutError on rate/timeout limits so
    the orchestrator can leave in_progress=True for automatic retry.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.work") as span:
        g = get_github_client()
        owner, repo, number = parse_issue_url(issue_url)
        issue_state = get_issue_state(issue_url)
        span.set_attribute("issue.number", number)
        span.set_attribute("issue.owner", owner)
        span.set_attribute("issue.repo", repo)

        issue = fetch_issue(g, owner, repo, number)
        default_branch = get_default_branch(g, owner, repo)
        repo_path = ensure_repo(owner, repo, default_branch)

        branch_name = issue_state.get("branch_name") or f"clayde/issue-{number}"
        pr_url = issue_state.get("pr_url")

        # Build visible comment text
        all_comments = fetch_issue_comments(g, owner, repo, number)
        visible_comments = filter_comments(all_comments)
        comments_text = "\n---\n".join(
            f"@{c.user.login}:\n{c.body}" for c in visible_comments
        ) or "(none)"

        body_text = issue.body or "(empty)"
        if not is_issue_visible(issue):
            body_text = "(filtered)"

        labels = ", ".join(lb.name for lb in issue.labels) or "none"

        # Build PR review text if a PR is already open
        review_text = ""
        if pr_url:
            try:
                _, _, pr_number = parse_pr_url(pr_url)
                reviews = get_pr_reviews(g, owner, repo, pr_number)
                review_comments = get_pr_review_comments(g, owner, repo, pr_number)
                review_text = _format_reviews(reviews, review_comments)
            except Exception as e:
                log.warning("[%s] Failed to fetch PR reviews: %s", issue_ref(owner, repo, number), e)

        # Persist metadata and branch_name before invoking Claude
        update_issue_state(issue_url, {
            "owner": owner, "repo": repo, "number": number,
            "issue_title": issue.title, "branch_name": branch_name,
        })

        prompt = render_template(
            "work.j2",
            number=number,
            title=issue.title,
            owner=owner,
            repo=repo,
            labels=labels,
            body=body_text,
            comments_text=comments_text,
            review_text=review_text,
            repo_path=repo_path,
            branch_name=branch_name,
            pr_url=pr_url or "",
            default_branch=default_branch,
        )

        log.info("[%s: %s] Invoking Claude", issue_ref(owner, repo, number), issue.title)

        # UsageLimitError/InvocationTimeoutError propagate to the orchestrator
        result = invoke_claude(prompt, repo_path)

        span.set_attribute("work.output_length", len(result.output or ""))

        # Parse summary (best-effort; fall back to raw output snippet)
        summary = None
        try:
            parsed = parse_response(result.output, WorkResponse)
            summary = parsed.summary
        except ValueError:
            log.warning("[%s: %s] Failed to parse work response JSON — using raw output",
                        issue_ref(owner, repo, number), issue.title)
            summary = (result.output or "").strip()[:500] or None

        if summary:
            post_comment(g, owner, repo, number,
                         f"{summary}{format_cost_line(result.cost_eur)}")

        # Detect PR opened by Claude and persist it
        detected_pr_url = find_open_pr(g, owner, repo, branch_name)
        if detected_pr_url:
            if not pr_url:
                _assign_reviewer(g, owner, repo, number, detected_pr_url)
            update_issue_state(issue_url, {"pr_url": detected_pr_url})

        span.set_attribute("work.pr_url", detected_pr_url or "")
        log.info("[%s: %s] Work complete", issue_ref(owner, repo, number), issue.title)


def _assign_reviewer(g, owner: str, repo: str, number: int, pr_url: str) -> None:
    """Assign the issue author as PR reviewer (skip if self)."""
    try:
        _, _, pr_number = parse_pr_url(pr_url)
        issue_author = get_issue_author(g, owner, repo, number)
        settings = get_settings()
        if issue_author.lower() == settings.github_username.lower():
            log.info("[%s] Issue author is self — skipping reviewer assignment",
                     issue_ref(owner, repo, number))
            return
        add_pr_reviewer(g, owner, repo, pr_number, issue_author)
    except Exception as e:
        log.warning("[%s] Failed to assign reviewer for %s: %s",
                    issue_ref(owner, repo, number), pr_url, e)


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
