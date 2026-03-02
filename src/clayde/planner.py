"""Plan phase — research repo, produce plan, post as issue comment."""

import logging
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from clayde.claude import UsageLimitError, invoke_claude
from clayde.github import (
    ensure_repo,
    fetch_issue,
    fetch_issue_comments,
    parse_issue_url,
    post_comment,
)
from clayde.state import update_issue_state

log = logging.getLogger("clayde.planner")

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def do_plan(issue_url):
    owner, repo, number = parse_issue_url(issue_url)
    update_issue_state(issue_url, {
        "status": "planning", "owner": owner, "repo": repo, "number": number,
    })

    issue = fetch_issue(owner, repo, number)
    repo_path = ensure_repo(owner, repo)

    labels = ", ".join(l["name"] for l in issue.get("labels", [])) or "none"
    comments = fetch_issue_comments(owner, repo, number)
    comments_text = "\n".join(
        f"@{c['user']['login']}:\n{c['body']}\n---" for c in comments
    ) or "(none)"

    template_src = (_PROMPTS_DIR / "plan.j2").read_text()
    prompt = Environment(undefined=StrictUndefined).from_string(template_src).render(
        number=number,
        title=issue["title"],
        owner=owner,
        repo=repo,
        labels=labels,
        body=issue.get("body") or "(empty)",
        comments_text=comments_text,
        repo_path=repo_path,
    )

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

    comment_body = (
        f"## Implementation Plan\n\n"
        f"{plan_text}\n\n"
        f"---\n"
        f"*React with \U0001f44d to approve this plan and start implementation.*"
    )
    comment_id = post_comment(owner, repo, number, comment_body)

    update_issue_state(issue_url, {
        "status": "awaiting_approval",
        "plan_comment_id": comment_id,
    })
    log.info("Plan posted for issue #%d (comment %s)", number, comment_id)
