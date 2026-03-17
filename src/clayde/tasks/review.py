"""Review task — address PR review comments and push updates."""

import logging

from clayde.claude import UsageLimitError, format_cost_line, invoke_claude
from clayde.config import DATA_DIR, get_github_client, get_settings
from clayde.git import ensure_repo
from clayde.prompts import render_template
from clayde.github import (
    fetch_issue,
    get_default_branch,
    get_pr_review_comments,
    get_pr_reviews,
    issue_ref,
    parse_issue_url,
    parse_pr_url,
    post_comment,
)
from clayde.state import IssueStatus, accumulate_cost, get_issue_state, pop_accumulated_cost, update_issue_state
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.tasks.review")


def run(issue_url: str) -> None:
    """Check for new PR reviews, address comments, push updates."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.review") as span:
        g = get_github_client()
        owner, repo, number = parse_issue_url(issue_url)
        issue_state = get_issue_state(issue_url)
        pr_url = issue_state.get("pr_url")
        span.set_attribute("issue.number", number)
        span.set_attribute("issue.owner", owner)
        span.set_attribute("issue.repo", repo)

        issue_title = issue_state.get("pr_title") or issue_state.get("issue_title") or "(title unknown)"
        issue_label = f"{issue_ref(owner, repo, number)}: {issue_title}"

        if not pr_url:
            log.warning("[%s] No PR URL in state — skipping review", issue_label)
            return

        _, _, pr_number = parse_pr_url(pr_url)
        last_seen_review_id = issue_state.get("last_seen_review_id", 0)
        github_username = get_settings().github_username

        # Get all reviews and filter to new ones
        reviews = get_pr_reviews(g, owner, repo, pr_number)
        new_reviews = [
            r for r in reviews
            if r.id > last_seen_review_id
            and r.user.login != github_username
        ]

        if not new_reviews:
            log.info("[%s] No new reviews on PR #%d", issue_label, pr_number)
            return

        # Check if any new review has actual content (comments or body)
        review_comments = get_pr_review_comments(g, owner, repo, pr_number)
        new_review_ids = {r.id for r in new_reviews}
        relevant_comments = [
            rc for rc in review_comments
            if rc.pull_request_review_id in new_review_ids
        ]

        # Also include reviews with body text
        review_bodies = [
            r for r in new_reviews
            if r.body and r.body.strip()
        ]

        if not relevant_comments and not review_bodies:
            # Reviews with no comments (e.g. just "approved") — update seen ID
            max_review_id = max(r.id for r in new_reviews)
            update_issue_state(issue_url, {"last_seen_review_id": max_review_id})

            # Check if any review is an approval
            approved = any(r.state == "APPROVED" for r in new_reviews)
            if approved:
                log.info("[%s] PR #%d approved — marking as done", issue_label, pr_number)
                update_issue_state(issue_url, {"status": IssueStatus.DONE})
                span.set_attribute("review.status", "approved")
            return

        # Build review text for Claude
        review_text = _format_reviews(new_reviews, relevant_comments)

        log.info("[%s] Addressing %d new review(s) on PR #%d",
                 issue_label, len(new_reviews), pr_number)

        update_issue_state(issue_url, {"status": IssueStatus.ADDRESSING_REVIEW})

        issue = fetch_issue(g, owner, repo, number)
        default_branch = get_default_branch(g, owner, repo)
        repo_path = ensure_repo(owner, repo, default_branch)
        branch_name = issue_state.get("branch_name", f"clayde/issue-{number}")

        prompt = _build_prompt(
            issue, owner, repo, number, repo_path, branch_name, review_text,
        )

        conversation_path = DATA_DIR / "conversations" / f"{owner}__{repo}__issue-{number}-review.json"

        try:
            result = invoke_claude(
                prompt,
                repo_path,
                branch_name=branch_name,
                conversation_path=conversation_path,
            )
        except UsageLimitError as e:
            log.warning("[%s] Usage limit hit during review handling", issue_label)
            accumulate_cost(issue_url, e.cost_eur)
            span.set_attribute("review.status", "limit")
            update_issue_state(issue_url, {
                "status": IssueStatus.INTERRUPTED,
                "interrupted_phase": IssueStatus.ADDRESSING_REVIEW,
            })
            return

        output = result.output
        total_cost = pop_accumulated_cost(issue_url) + result.cost_eur

        # Post summary comment on the issue
        if output and output.strip():
            post_comment(g, owner, repo, number,
                         f"**Review addressed.** {output.strip()}{format_cost_line(total_cost)}")

        # Update last seen review ID and return to pr_open
        max_review_id = max(r.id for r in new_reviews)
        update_issue_state(issue_url, {
            "status": IssueStatus.PR_OPEN,
            "last_seen_review_id": max_review_id,
        })
        span.set_attribute("review.status", "addressed")
        log.info("[%s] Review comments addressed", issue_label)


def _format_reviews(reviews: list, review_comments: list) -> str:
    """Format reviews and review comments into text for the prompt."""
    parts = []

    for review in reviews:
        header = f"Review by @{review.user.login} (state: {review.state}):"
        inline_comments = [rc for rc in review_comments if rc.pull_request_review_id == review.id]

        has_body = review.body and review.body.strip()
        if has_body or inline_comments:
            parts.append(f"{header}\n{review.body}" if has_body else header)

        for rc in inline_comments:
            file_info = f"  File: {rc.path}"
            if hasattr(rc, "line") and rc.line:
                file_info += f", line {rc.line}"
            parts.append(f"{file_info}\n  {rc.body}")

    return "\n---\n".join(parts) or "(no review content)"


def _build_prompt(issue, owner, repo, number, repo_path, branch_name, review_text) -> str:
    return render_template(
        "address_review.j2",
        number=number,
        title=issue.title,
        owner=owner,
        repo=repo,
        body=issue.body or "(empty)",
        branch_name=branch_name,
        review_text=review_text,
        repo_path=repo_path,
    )
