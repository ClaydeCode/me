"""Implement task — implement the approved plan, open PR, assign reviewer.

After implementation, assigns the issue author as PR reviewer and sets
status to ``pr_open`` for review monitoring.
"""

import logging
import subprocess

from jinja2 import Environment, StrictUndefined

from clayde.claude import UsageLimitError, format_cost_line, invoke_claude
from clayde.config import DATA_DIR, get_github_client
from clayde.git import ensure_repo
from clayde.prompts import PROMPTS_DIR
from clayde.github import (
    add_pr_reviewer,
    create_pull_request,
    extract_branch_name,
    fetch_comment,
    fetch_issue,
    fetch_issue_comments,
    find_open_pr,
    get_default_branch,
    get_issue_author,
    parse_issue_url,
    parse_pr_url,
    post_comment,
)
from clayde.safety import filter_comments
from clayde.state import accumulate_cost, get_issue_state, pop_accumulated_cost, update_issue_state
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.tasks.implement")


def run(issue_url: str) -> None:
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.task.implement") as span:
        g = get_github_client()
        owner, repo, number = parse_issue_url(issue_url)
        issue_state = get_issue_state(issue_url)
        plan_comment_id = issue_state["plan_comment_id"]
        span.set_attribute("issue.number", number)
        span.set_attribute("issue.owner", owner)
        span.set_attribute("issue.repo", repo)

        resumed = issue_state.get("status") == "interrupted"
        span.set_attribute("implement.resumed_from_interrupted", resumed)

        # If resuming from an interrupted implementation, check for an existing PR.
        if resumed:
            branch_name = issue_state.get("branch_name", f"clayde/issue-{number}")
            existing_pr = find_open_pr(g, owner, repo, branch_name)
            if existing_pr:
                log.info("Resuming interrupted #%d — found existing PR %s", number, existing_pr)
                accumulated = pop_accumulated_cost(issue_url)
                _assign_reviewer_and_finish(
                    g, owner, repo, number, issue_url, existing_pr, span,
                    cost_eur=accumulated if accumulated > 0 else None,
                )
                return

        update_issue_state(issue_url, {"status": "implementing"})

        issue = fetch_issue(g, owner, repo, number)
        default_branch = get_default_branch(g, owner, repo)
        repo_path = ensure_repo(owner, repo, default_branch)

        plan_comment = fetch_comment(g, owner, repo, number, plan_comment_id)
        plan_text = plan_comment.body

        branch_name = issue_state.get("branch_name") or extract_branch_name(plan_text, number)
        update_issue_state(issue_url, {"branch_name": branch_name})

        # If resuming an interrupted implementation, checkout the WIP branch
        if resumed and branch_name:
            _checkout_wip_branch(repo_path, branch_name)

        all_comments = fetch_issue_comments(g, owner, repo, number)
        visible_comments = filter_comments(all_comments)
        discussion_text = _collect_discussion(visible_comments, plan_comment_id)

        prompt = _build_prompt(issue, plan_text, discussion_text, owner, repo, number, repo_path, branch_name)

        conv_path = DATA_DIR / "conversations" / f"{owner}__{repo}__issue-{number}.json"
        conv_path.parent.mkdir(parents=True, exist_ok=True)

        log.info("Invoking Claude for implementation of issue #%d", number)
        try:
            result = invoke_claude(
                prompt,
                repo_path,
                branch_name=branch_name,
                conversation_path=conv_path,
            )
        except UsageLimitError as e:
            log.warning("Usage limit hit during implementation #%d — will retry next cycle", number)
            accumulate_cost(issue_url, e.cost_eur)
            log.info("Conversation saved to %s", conv_path)
            span.set_attribute("implement.status", "limit")
            update_issue_state(issue_url, {"status": "interrupted", "interrupted_phase": "implementing"})
            return

        output = result.output
        total_cost = pop_accumulated_cost(issue_url) + result.cost_eur

        # Check for existing PR first (e.g. from a previous interrupted run)
        pr_url = find_open_pr(g, owner, repo, branch_name)
        if not pr_url:
            # Try to create a new PR
            try:
                issue_obj = fetch_issue(g, owner, repo, number)
                pr_url = create_pull_request(
                    g, owner, repo,
                    title=f"Fix #{number}: {issue_obj.title}",
                    body=f"Closes #{number}{format_cost_line(total_cost)}",
                    head=branch_name,
                    base=default_branch,
                )
                log.info("Created PR: %s", pr_url)
            except Exception as e:
                log.error("Failed to create PR for #%d: %s", number, e)

        if pr_url:
            _assign_reviewer_and_finish(g, owner, repo, number, issue_url, pr_url, span,
                                        cost_eur=total_cost)
            log.info("Conversation saved to %s", conv_path)
        else:
            log.error("Implementation of #%d produced no PR", number)
            log.error("Claude output (last 2000 chars): %s", (output or "")[-2000:])
            retry_count = issue_state.get("retry_count", 0) + 1
            if retry_count >= 3:
                log.error("Issue #%d failed after %d retries — giving up", number, retry_count)
                post_comment(g, owner, repo, number,
                             "Implementation failed to produce a PR after multiple retries. "
                             "Marking as failed — manual intervention needed.")
                span.set_attribute("implement.status", "failed")
                update_issue_state(issue_url, {"status": "failed", "retry_count": retry_count})
            else:
                post_comment(g, owner, repo, number,
                             f"Implementation ran but no PR was created (attempt {retry_count}/3). "
                             "I'll retry on the next cycle.")
                span.set_attribute("implement.status", "no_pr")
                span.set_attribute("implement.retry_count", retry_count)
                update_issue_state(issue_url, {"status": "interrupted", "interrupted_phase": "implementing",
                                               "retry_count": retry_count})


def _assign_reviewer_and_finish(g, owner, repo, number, issue_url, pr_url, span,
                                cost_eur=None):
    """Post result, assign reviewer, set status to pr_open."""
    _post_result(g, owner, repo, number, pr_url, cost_eur=cost_eur)

    # Assign the issue author as PR reviewer
    try:
        issue_author = get_issue_author(g, owner, repo, number)
        _, _, pr_number = parse_pr_url(pr_url)
        add_pr_reviewer(g, owner, repo, pr_number, issue_author)
    except Exception as e:
        log.warning("Failed to assign reviewer for PR %s: %s", pr_url, e)

    update_issue_state(issue_url, {
        "status": "pr_open",
        "pr_url": pr_url,
        "last_seen_review_id": 0,
    })
    span.set_attribute("implement.status", "pr_open")
    span.set_attribute("implement.pr_url", pr_url)
    log.info("Issue #%d PR open — monitoring for reviews: %s", number, pr_url)


def _checkout_wip_branch(repo_path, branch_name: str) -> None:
    """Checkout an existing WIP branch if it exists (locally or on remote)."""
    cwd = str(repo_path)

    # Check local branch
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=cwd, capture_output=True, text=True,
    )
    if result.stdout.strip():
        subprocess.run(["git", "checkout", branch_name], cwd=cwd, capture_output=True)
        subprocess.run(["git", "pull", "origin", branch_name], cwd=cwd, capture_output=True)
        log.info("Resumed WIP branch %s (local)", branch_name)
        return

    # Check remote branch
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch_name],
        cwd=cwd, capture_output=True, text=True,
    )
    if result.stdout.strip():
        subprocess.run(
            ["git", "checkout", "-b", branch_name, f"origin/{branch_name}"],
            cwd=cwd, capture_output=True,
        )
        log.info("Resumed WIP branch %s (remote)", branch_name)
        return

    log.info("No existing WIP branch %s found — starting fresh", branch_name)


def _collect_discussion(all_comments, plan_comment_id: int) -> str:
    found_plan = False
    discussion = []
    for c in all_comments:
        if c.id == plan_comment_id:
            found_plan = True
            continue
        if found_plan:
            discussion.append(f"@{c.user.login}:\n{c.body}")
    return "\n---\n".join(discussion) or "(none)"


def _build_prompt(issue, plan_text: str, discussion_text: str, owner: str, repo: str, number: int, repo_path: str, branch_name: str) -> str:
    template_src = (PROMPTS_DIR / "implement.j2").read_text()
    return Environment(undefined=StrictUndefined).from_string(template_src).render(
        number=number,
        title=issue.title,
        owner=owner,
        repo=repo,
        body=issue.body or "(empty)",
        plan_text=plan_text,
        discussion_text=discussion_text,
        repo_path=repo_path,
        branch_name=branch_name,
    )


def _post_result(g, owner: str, repo: str, number: int, pr_url: str,
                 cost_eur: float | None = None) -> None:
    body = f"Implementation complete — PR opened: {pr_url}"
    if cost_eur is not None:
        body += format_cost_line(cost_eur)
    post_comment(g, owner, repo, number, body)
