"""Implement task — implement the approved plan, open PR, post result."""

import logging
import re
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from clayde.claude import UsageLimitError, invoke_claude
from clayde.config import get_github_client
from clayde.git import ensure_repo
from clayde.github import (
    fetch_comment,
    fetch_issue,
    fetch_issue_comments,
    find_open_pr,
    get_default_branch,
    parse_issue_url,
    post_comment,
)
from clayde.state import get_issue_state, update_issue_state

log = logging.getLogger("clayde.tasks.implement")

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def run(issue_url: str) -> None:
    g = get_github_client()
    owner, repo, number = parse_issue_url(issue_url)
    issue_state = get_issue_state(issue_url)
    plan_comment_id = issue_state["plan_comment_id"]

    # If resuming from an interrupted implementation, check for an existing PR.
    if issue_state.get("status") == "interrupted":
        existing_pr = find_open_pr(g, owner, repo, number)
        if existing_pr:
            log.info("Resuming interrupted #%d — found existing PR %s", number, existing_pr)
            post_comment(g, owner, repo, number, f"Implementation complete — PR opened: {existing_pr}")
            update_issue_state(issue_url, {"status": "done", "pr_url": existing_pr})
            return

    update_issue_state(issue_url, {"status": "implementing"})

    issue = fetch_issue(g, owner, repo, number)
    default_branch = get_default_branch(g, owner, repo)
    repo_path = ensure_repo(owner, repo, default_branch)

    plan_comment = fetch_comment(g, owner, repo, plan_comment_id)
    plan_text = plan_comment.body

    all_comments = fetch_issue_comments(g, owner, repo, number)
    discussion_text = _collect_discussion(all_comments, plan_comment_id)

    prompt = _build_prompt(issue, plan_text, discussion_text, owner, repo, number, repo_path)

    log.info("Invoking Claude for implementation of issue #%d", number)
    try:
        output = invoke_claude(prompt, repo_path)
    except UsageLimitError:
        log.warning("Usage limit hit during implementation #%d — will retry next cycle", number)
        update_issue_state(issue_url, {"status": "interrupted", "interrupted_phase": "implementing"})
        return

    pr_url = _extract_pr_url(output)
    _post_result(g, owner, repo, number, pr_url)
    update_issue_state(issue_url, {"status": "done", "pr_url": pr_url})
    log.info("Issue #%d done%s", number, f" — PR: {pr_url}" if pr_url else "")


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


def _build_prompt(issue, plan_text: str, discussion_text: str, owner: str, repo: str, number: int, repo_path: str) -> str:
    template_src = (_PROMPTS_DIR / "implement.j2").read_text()
    return Environment(undefined=StrictUndefined).from_string(template_src).render(
        number=number,
        title=issue.title,
        owner=owner,
        repo=repo,
        body=issue.body or "(empty)",
        plan_text=plan_text,
        discussion_text=discussion_text,
        repo_path=repo_path,
    )


def _extract_pr_url(output: str) -> str | None:
    if not output:
        return None
    for line in reversed(output.strip().splitlines()):
        m = re.search(r"https://github\.com/\S+/pull/\d+", line)
        if m:
            return m.group(0)
    return None


def _post_result(g, owner: str, repo: str, number: int, pr_url: str | None) -> None:
    if pr_url:
        body = f"Implementation complete — PR opened: {pr_url}"
    else:
        body = (
            "I attempted to implement the plan but could not confirm a PR was created. "
            "Please check the repository for any branches or changes."
        )
    post_comment(g, owner, repo, number, body)
