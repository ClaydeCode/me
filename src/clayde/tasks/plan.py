"""Plan task — research repo, produce plan, post as issue comment."""

import logging
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from clayde.claude import UsageLimitError, invoke_claude
from clayde.config import get_github_client
from clayde.git import ensure_repo
from clayde.github import (
    fetch_issue,
    fetch_issue_comments,
    get_default_branch,
    parse_issue_url,
    post_comment,
)
from clayde.state import update_issue_state

log = logging.getLogger("clayde.tasks.plan")

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def run(issue_url: str) -> None:
    g = get_github_client()
    owner, repo, number = parse_issue_url(issue_url)
    update_issue_state(issue_url, {
        "status": "planning", "owner": owner, "repo": repo, "number": number,
    })

    issue = fetch_issue(g, owner, repo, number)
    default_branch = get_default_branch(g, owner, repo)
    repo_path = ensure_repo(owner, repo, default_branch)

    prompt = _build_prompt(g, issue, owner, repo, number, repo_path)

    log.info("Invoking Claude for planning issue #%d", number)
    try:
        plan_text = invoke_claude(prompt, repo_path)
    except UsageLimitError:
        log.warning("Usage limit hit during planning #%d — will retry next cycle", number)
        update_issue_state(issue_url, {"status": "interrupted", "interrupted_phase": "planning"})
        return

    if not plan_text.strip():
        log.error("Claude returned empty plan for issue #%d", number)
        update_issue_state(issue_url, {"status": "failed"})
        return

    # Reject suspiciously short output that may be a rate limit message
    if len(plan_text.strip()) < 200:
        log.warning("Claude returned very short plan for issue #%d (%d chars) — treating as failed",
                     number, len(plan_text.strip()))
        update_issue_state(issue_url, {"status": "interrupted", "interrupted_phase": "planning"})
        return

    comment_id = _post_plan_comment(g, owner, repo, number, plan_text)
    update_issue_state(issue_url, {
        "status": "awaiting_approval",
        "plan_comment_id": comment_id,
    })
    log.info("Plan posted for issue #%d (comment %s)", number, comment_id)


def _build_prompt(g, issue, owner: str, repo: str, number: int, repo_path: str) -> str:
    labels = ", ".join(l.name for l in issue.labels) or "none"
    comments = fetch_issue_comments(g, owner, repo, number)
    comments_text = "\n".join(
        f"@{c.user.login}:\n{c.body}\n---" for c in comments
    ) or "(none)"

    template_src = (_PROMPTS_DIR / "plan.j2").read_text()
    return Environment(undefined=StrictUndefined).from_string(template_src).render(
        number=number,
        title=issue.title,
        owner=owner,
        repo=repo,
        labels=labels,
        body=issue.body or "(empty)",
        comments_text=comments_text,
        repo_path=repo_path,
    )


def _post_plan_comment(g, owner: str, repo: str, number: int, plan_text: str) -> int:
    comment_body = (
        f"## Implementation Plan\n\n"
        f"{plan_text}\n\n"
        f"---\n"
        f"*React with \U0001f44d to approve this plan and start implementation.*"
    )
    return post_comment(g, owner, repo, number, comment_body)
