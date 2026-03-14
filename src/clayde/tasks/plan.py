"""Plan task — two-phase planning with preliminary and thorough plans.

Phase 1 (preliminary): Post a short overview with questions.
Phase 2 (thorough): After preliminary approval, post the full detailed plan.
Updates: When new visible comments arrive, update the current plan and post a
         summary of changes.
"""

import logging

from github import Github
from github.Issue import Issue

from clayde.claude import UsageLimitError, format_cost_line, invoke_claude
from clayde.config import get_github_client
from clayde.git import ensure_repo
from clayde.github import (
    edit_comment,
    fetch_comment,
    fetch_issue,
    fetch_issue_comments,
    get_default_branch,
    parse_issue_url,
    post_comment,
)
from clayde.prompts import collect_comments_after, render_template
from clayde.safety import filter_comments, get_new_visible_comments, is_issue_visible
from clayde.state import IssueStatus, accumulate_cost, get_issue_state, pop_accumulated_cost, update_issue_state
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.tasks.plan")

_UPDATE_PLAN_SEPARATOR = "---UPDATED_PLAN---"


# ---------------------------------------------------------------------------
# Phase 1: Preliminary plan
# ---------------------------------------------------------------------------

def run_preliminary(issue_url: str) -> None:
    """Research the issue and post a short preliminary plan with questions."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.preliminary_plan") as span:
        g = get_github_client()
        owner, repo, number = parse_issue_url(issue_url)
        span.set_attribute("issue.number", number)
        span.set_attribute("issue.owner", owner)
        span.set_attribute("issue.repo", repo)
        update_issue_state(issue_url, {
            "status": IssueStatus.PRELIMINARY_PLANNING,
            "owner": owner, "repo": repo, "number": number,
        })

        issue = fetch_issue(g, owner, repo, number)
        default_branch = get_default_branch(g, owner, repo)
        repo_path = ensure_repo(owner, repo, default_branch)

        prompt = _build_preliminary_prompt(g, issue, owner, repo, number, repo_path)

        log.info("Invoking Claude for preliminary plan on issue #%d", number)
        try:
            result = invoke_claude(prompt, repo_path)
        except UsageLimitError as e:
            log.warning("Usage limit hit during preliminary planning #%d", number)
            accumulate_cost(issue_url, e.cost_eur)
            span.set_attribute("plan.status", "limit")
            update_issue_state(issue_url, {
                "status": IssueStatus.INTERRUPTED,
                "interrupted_phase": IssueStatus.PRELIMINARY_PLANNING,
            })
            return

        plan_text = result.output
        total_cost = pop_accumulated_cost(issue_url) + result.cost_eur
        span.set_attribute("plan.output_length", len(plan_text))

        if not plan_text.strip():
            log.error("Claude returned empty preliminary plan for issue #%d", number)
            span.set_attribute("plan.status", "empty")
            update_issue_state(issue_url, {"status": IssueStatus.FAILED})
            return

        if len(plan_text.strip()) < 100:
            log.warning("Claude returned very short preliminary plan for issue #%d (%d chars)",
                        number, len(plan_text.strip()))
            span.set_attribute("plan.status", "short")
            update_issue_state(issue_url, {
                "status": IssueStatus.INTERRUPTED,
                "interrupted_phase": IssueStatus.PRELIMINARY_PLANNING,
            })
            return

        comment_id = _post_preliminary_comment(g, owner, repo, number, plan_text, total_cost)

        # Track the last seen comment so we can detect new ones later
        all_comments = fetch_issue_comments(g, owner, repo, number)
        last_comment_id = all_comments[-1].id if all_comments else 0

        update_issue_state(issue_url, {
            "status": IssueStatus.AWAITING_PRELIMINARY_APPROVAL,
            "preliminary_comment_id": comment_id,
            "last_seen_comment_id": last_comment_id,
        })
        span.set_attribute("plan.status", "posted")
        log.info("Preliminary plan posted for issue #%d (comment %s)", number, comment_id)


# ---------------------------------------------------------------------------
# Phase 2: Thorough plan
# ---------------------------------------------------------------------------

def run_thorough(issue_url: str) -> None:
    """Post a thorough implementation plan (after preliminary approval)."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.thorough_plan") as span:
        g = get_github_client()
        owner, repo, number = parse_issue_url(issue_url)
        span.set_attribute("issue.number", number)
        span.set_attribute("issue.owner", owner)
        span.set_attribute("issue.repo", repo)
        update_issue_state(issue_url, {"status": IssueStatus.PLANNING})

        issue = fetch_issue(g, owner, repo, number)
        default_branch = get_default_branch(g, owner, repo)
        repo_path = ensure_repo(owner, repo, default_branch)

        issue_state = get_issue_state(issue_url)
        preliminary_comment_id = issue_state.get("preliminary_comment_id")
        preliminary_comment = fetch_comment(g, owner, repo, number, preliminary_comment_id)
        preliminary_text = preliminary_comment.body

        # Collect discussion after the preliminary plan
        all_comments = fetch_issue_comments(g, owner, repo, number)
        visible_comments = filter_comments(all_comments)
        discussion_text = collect_comments_after(visible_comments, preliminary_comment_id)

        prompt = _build_thorough_prompt(
            g, issue, owner, repo, number, repo_path,
            preliminary_text, discussion_text,
        )

        log.info("Invoking Claude for thorough plan on issue #%d", number)
        try:
            result = invoke_claude(prompt, repo_path)
        except UsageLimitError as e:
            log.warning("Usage limit hit during thorough planning #%d", number)
            accumulate_cost(issue_url, e.cost_eur)
            span.set_attribute("plan.status", "limit")
            update_issue_state(issue_url, {
                "status": IssueStatus.INTERRUPTED,
                "interrupted_phase": IssueStatus.PLANNING,
            })
            return

        plan_text = result.output
        total_cost = pop_accumulated_cost(issue_url) + result.cost_eur
        span.set_attribute("plan.output_length", len(plan_text))

        if not plan_text.strip():
            log.error("Claude returned empty thorough plan for issue #%d", number)
            span.set_attribute("plan.status", "empty")
            update_issue_state(issue_url, {"status": IssueStatus.FAILED})
            return

        if len(plan_text.strip()) < 200:
            log.warning("Claude returned very short thorough plan for issue #%d (%d chars)",
                        number, len(plan_text.strip()))
            span.set_attribute("plan.status", "short")
            update_issue_state(issue_url, {
                "status": IssueStatus.INTERRUPTED,
                "interrupted_phase": IssueStatus.PLANNING,
            })
            return

        comment_id = _post_thorough_plan_comment(g, owner, repo, number, plan_text, total_cost)

        # Update last seen comment
        all_comments = fetch_issue_comments(g, owner, repo, number)
        last_comment_id = all_comments[-1].id if all_comments else 0

        update_issue_state(issue_url, {
            "status": IssueStatus.AWAITING_PLAN_APPROVAL,
            "plan_comment_id": comment_id,
            "last_seen_comment_id": last_comment_id,
        })
        span.set_attribute("plan.status", "posted")
        log.info("Thorough plan posted for issue #%d (comment %s)", number, comment_id)


# ---------------------------------------------------------------------------
# Plan update (new comments detected)
# ---------------------------------------------------------------------------

def run_update(issue_url: str, phase: str) -> None:
    """Process new visible comments and update the current plan.

    Args:
        issue_url: The issue URL.
        phase: Either "preliminary" or "thorough" — which plan to update.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.update_plan") as span:
        g = get_github_client()
        owner, repo, number = parse_issue_url(issue_url)
        span.set_attribute("issue.number", number)
        span.set_attribute("plan.update_phase", phase)

        issue_state = get_issue_state(issue_url)

        if phase == "preliminary":
            plan_comment_id = issue_state.get("preliminary_comment_id")
            return_status = IssueStatus.AWAITING_PRELIMINARY_APPROVAL
        elif phase == "thorough":
            plan_comment_id = issue_state.get("plan_comment_id")
            return_status = IssueStatus.AWAITING_PLAN_APPROVAL
        else:
            raise ValueError(f"Unknown plan update phase: {phase!r}")

        last_seen = issue_state.get("last_seen_comment_id", 0)

        issue = fetch_issue(g, owner, repo, number)
        default_branch = get_default_branch(g, owner, repo)
        repo_path = ensure_repo(owner, repo, default_branch)

        plan_comment = fetch_comment(g, owner, repo, number, plan_comment_id)
        current_plan_text = plan_comment.body

        all_comments = fetch_issue_comments(g, owner, repo, number)
        new_comments = get_new_visible_comments(all_comments, last_seen)

        if not new_comments:
            log.info("No new visible comments for issue #%d — skipping update", number)
            return

        new_comments_text = "\n---\n".join(
            f"@{c.user.login}:\n{c.body}" for c in new_comments
        )

        body_text = issue.body or "(empty)"
        if not is_issue_visible(issue):
            body_text = "(filtered)"

        prompt = _build_update_prompt(
            number, issue.title, owner, repo,
            body_text, current_plan_text, new_comments_text, repo_path,
        )

        log.info("Invoking Claude for plan update on issue #%d (%s phase)", number, phase)
        try:
            result = invoke_claude(prompt, repo_path)
        except UsageLimitError as e:
            log.warning("Usage limit hit during plan update #%d", number)
            accumulate_cost(issue_url, e.cost_eur)
            span.set_attribute("plan.update_status", "limit")
            update_issue_state(issue_url, {
                "status": IssueStatus.INTERRUPTED,
                "interrupted_phase": IssueStatus.PRELIMINARY_PLANNING if phase == "preliminary" else IssueStatus.PLANNING,
            })
            return

        total_cost = pop_accumulated_cost(issue_url) + result.cost_eur

        # Parse output into summary + updated plan
        summary, updated_plan = _parse_update_output(result.output)

        if updated_plan:
            # Edit the existing plan comment
            edit_comment(g, owner, repo, number, plan_comment_id, updated_plan)
            log.info("Updated %s plan comment %d for issue #%d", phase, plan_comment_id, number)

        if summary:
            # Post a new comment with the change summary
            post_comment(g, owner, repo, number,
                         f"**Plan updated.** {summary}{format_cost_line(total_cost)}")

        # Update last seen comment
        all_comments = fetch_issue_comments(g, owner, repo, number)
        last_comment_id = all_comments[-1].id if all_comments else 0

        update_issue_state(issue_url, {
            "status": return_status,
            "last_seen_comment_id": last_comment_id,
        })
        span.set_attribute("plan.update_status", "updated")
        log.info("Plan update complete for issue #%d", number)



# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_preliminary_prompt(g: Github, issue: Issue, owner: str, repo: str, number: int, repo_path: str) -> str:
    labels = ", ".join(l.name for l in issue.labels) or "none"
    comments = fetch_issue_comments(g, owner, repo, number)
    visible = filter_comments(comments)
    comments_text = "\n".join(
        f"@{c.user.login}:\n{c.body}\n---" for c in visible
    ) or "(none)"

    body_text = issue.body or "(empty)"
    if not is_issue_visible(issue):
        body_text = "(filtered)"

    return render_template(
        "preliminary_plan.j2",
        number=number,
        title=issue.title,
        owner=owner,
        repo=repo,
        labels=labels,
        body=body_text,
        comments_text=comments_text,
        repo_path=repo_path,
    )


def _build_thorough_prompt(g: Github, issue: Issue, owner: str, repo: str, number: int,
                           repo_path: str, preliminary_text: str, discussion_text: str) -> str:
    labels = ", ".join(l.name for l in issue.labels) or "none"

    body_text = issue.body or "(empty)"
    if not is_issue_visible(issue):
        body_text = "(filtered)"

    return render_template(
        "thorough_plan.j2",
        number=number,
        title=issue.title,
        owner=owner,
        repo=repo,
        labels=labels,
        body=body_text,
        preliminary_plan_text=preliminary_text,
        discussion_text=discussion_text,
        repo_path=repo_path,
    )


def _build_update_prompt(number: int, title: str, owner: str, repo: str, body: str,
                         current_plan_text: str, new_comments_text: str, repo_path: str) -> str:
    return render_template(
        "update_plan.j2",
        number=number,
        title=title,
        owner=owner,
        repo=repo,
        body=body,
        current_plan_text=current_plan_text,
        new_comments_text=new_comments_text,
        repo_path=repo_path,
    )


# ---------------------------------------------------------------------------
# Comment formatting
# ---------------------------------------------------------------------------

def _post_preliminary_comment(g, owner: str, repo: str, number: int, plan_text: str,
                              cost_eur: float = 0.0) -> int:
    comment_body = (
        f"## Preliminary Plan\n\n"
        f"{plan_text}\n\n"
        f"---\n"
        f"*React with \U0001f44d to approve this preliminary plan and proceed "
        f"to a thorough implementation plan.*"
        f"{format_cost_line(cost_eur)}"
    )
    return post_comment(g, owner, repo, number, comment_body)


def _post_thorough_plan_comment(g, owner: str, repo: str, number: int, plan_text: str,
                                cost_eur: float = 0.0) -> int:
    comment_body = (
        f"## Implementation Plan\n\n"
        f"{plan_text}\n\n"
        f"---\n"
        f"*React with \U0001f44d to approve this plan and start implementation.*"
        f"{format_cost_line(cost_eur)}"
    )
    return post_comment(g, owner, repo, number, comment_body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_update_output(output: str) -> tuple[str, str]:
    """Parse Claude output into (summary, updated_plan).

    Expected format:
        <summary text>
        ---UPDATED_PLAN---
        <full updated plan>

    Returns (summary, updated_plan). If separator not found, treats entire
    output as summary with empty updated_plan.
    """
    if _UPDATE_PLAN_SEPARATOR in output:
        parts = output.split(_UPDATE_PLAN_SEPARATOR, 1)
        return parts[0].strip(), parts[1].strip()
    return output.strip(), ""
