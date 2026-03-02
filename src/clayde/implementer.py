"""Implement phase — implement the approved plan, open PR, post result."""

import logging
import re
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from clayde.claude import UsageLimitError, invoke_claude
from clayde.github import (
    ensure_repo,
    fetch_issue,
    fetch_issue_comments,
    gh_api,
    parse_issue_url,
    post_comment,
)
from clayde.state import get_issue_state, update_issue_state

log = logging.getLogger("clayde.implementer")

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _find_existing_pr(owner, repo, number):
    """Return the HTML URL of an open PR for clayde/issue-{number}, or None."""
    branch = f"clayde/issue-{number}"
    pulls = gh_api(f"/repos/{owner}/{repo}/pulls?state=open&head={owner}:{branch}")
    if pulls:
        return pulls[0]["html_url"]
    return None


def do_implement(issue_url):
    owner, repo, number = parse_issue_url(issue_url)
    issue_state = get_issue_state(issue_url)
    plan_comment_id = issue_state["plan_comment_id"]

    # If resuming from an interrupted implementation, check for an existing PR
    # to avoid duplicate branches / PRs on retry.
    if issue_state.get("status") == "interrupted":
        existing_pr = _find_existing_pr(owner, repo, number)
        if existing_pr:
            log.info("Resuming interrupted #%d — found existing PR %s", number, existing_pr)
            post_comment(owner, repo, number, f"Implementation complete — PR opened: {existing_pr}")
            update_issue_state(issue_url, {"status": "done", "pr_url": existing_pr})
            return

    update_issue_state(issue_url, {"status": "implementing"})

    issue = fetch_issue(owner, repo, number)
    repo_path = ensure_repo(owner, repo)

    # Fetch plan text
    plan_comment = gh_api(
        f"/repos/{owner}/{repo}/issues/comments/{plan_comment_id}"
    )
    plan_text = plan_comment["body"]

    # Fetch any discussion comments posted after the plan
    all_comments = fetch_issue_comments(owner, repo, number)
    found_plan = False
    discussion = []
    for c in all_comments:
        if c["id"] == plan_comment_id:
            found_plan = True
            continue
        if found_plan:
            discussion.append(f"@{c['user']['login']}:\n{c['body']}")

    discussion_text = "\n---\n".join(discussion) or "(none)"

    template_src = (_PROMPTS_DIR / "implement.j2").read_text()
    prompt = Environment(undefined=StrictUndefined).from_string(template_src).render(
        number=number,
        title=issue["title"],
        owner=owner,
        repo=repo,
        body=issue.get("body") or "(empty)",
        plan_text=plan_text,
        discussion_text=discussion_text,
        repo_path=repo_path,
    )

    log.info("Invoking Claude for implementation of issue #%d", number)
    try:
        output = invoke_claude(prompt, repo_path)
    except UsageLimitError:
        log.warning("Usage limit hit during implementation #%d — will retry next cycle", number)
        update_issue_state(issue_url, {"status": "interrupted", "interrupted_phase": "implementing"})
        return

    # Extract PR URL from output
    pr_url = None
    if output:
        for line in reversed(output.strip().splitlines()):
            m = re.search(r"https://github\.com/\S+/pull/\d+", line)
            if m:
                pr_url = m.group(0)
                break

    # Post result comment on the issue
    if pr_url:
        result_body = f"Implementation complete — PR opened: {pr_url}"
    else:
        result_body = (
            "I attempted to implement the plan but could not confirm a PR was created. "
            "Please check the repository for any branches or changes."
        )

    post_comment(owner, repo, number, result_body)
    update_issue_state(issue_url, {"status": "done", "pr_url": pr_url})
    log.info("Issue #%d done%s", number, f" — PR: {pr_url}" if pr_url else "")
