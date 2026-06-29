"""Phase handlers for the Freeshard stateless execution loop."""
import json
import logging
import subprocess
from pathlib import Path

from clayde.claude import invoke_claude
from clayde.freeshard.repos import verify_profile
from clayde.freeshard.verify import local_verify
from clayde.freeshard.worktree import add_worktree, remove_worktree
from clayde.github import (
    add_pr_reviewer,
    create_pull_request,
    fetch_issue,
    fetch_issue_comments,
    find_open_pr,
    post_comment,
)
from clayde.prompts import render_template

log = logging.getLogger("clayde.freeshard.steps")


def _branch(number: int) -> str:
    return f"clayde/issue-{number}"


def _push_branch(worktree: Path, branch: str) -> None:
    subprocess.run(["git", "push", "origin", branch], cwd=str(worktree), check=True)


def _build_discussion_text(comments) -> str:
    parts = []
    for c in comments:
        author = c.user.login if c.user else "unknown"
        parts.append(f"@{author}: {c.body}")
    return "\n\n".join(parts)


def run_implement(g, owner: str, repo: str, number: int, default_branch: str) -> None:
    """Worktree → render fs_implement.j2 → invoke Claude → local verify → push + open PR (if green)."""
    branch = _branch(number)
    if find_open_pr(g, owner, repo, branch):
        log.info("PR already open for %s/%s#%d — nothing to implement", owner, repo, number)
        return
    worktree = add_worktree(owner, repo, number, default_branch)

    issue = fetch_issue(g, owner, repo, number)
    comments = fetch_issue_comments(g, owner, repo, number)
    discussion_text = _build_discussion_text(comments)

    prompt = render_template(
        "fs_implement.j2",
        number=number,
        owner=owner,
        repo=repo,
        title=issue.title,
        body=issue.body or "",
        branch_name=branch,
        repo_path=str(worktree),
        discussion_text=discussion_text,
    )

    result = invoke_claude(prompt, str(worktree), branch_name=branch, conversation_path=None)

    profile = verify_profile(repo)
    ok, log_tail = local_verify(profile, worktree, repo=repo)

    if not ok:
        log.warning(
            "Local verify failed for %s/%s#%d — leaving WIP on branch: %s",
            owner, repo, number, log_tail[-200:],
        )
        return

    _push_branch(worktree, branch)

    summary = ""
    try:
        data = json.loads(result.output)
        summary = data.get("summary", "")
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    body = f"Closes #{number}"
    if summary:
        body += f"\n\n{summary}"

    create_pull_request(
        g, owner, repo,
        title=issue.title,
        body=body,
        head=branch,
        base=default_branch,
    )


def run_ci_fix(g, owner: str, repo: str, number: int, default_branch: str, ci_log: str) -> None:
    """Worktree → render fs_ci_fix.j2 → invoke Claude → push (CI re-runs on push)."""
    branch = _branch(number)
    worktree = add_worktree(owner, repo, number, default_branch)

    prompt = render_template(
        "fs_ci_fix.j2",
        number=number,
        owner=owner,
        repo=repo,
        branch_name=branch,
        repo_path=str(worktree),
        ci_log=ci_log,
    )

    invoke_claude(prompt, str(worktree), branch_name=branch, conversation_path=None)
    _push_branch(worktree, branch)


def run_handoff(g, owner: str, repo: str, number: int, pr_number: int, reviewer: str) -> None:
    """Assign reviewer, post ready-for-review comment, remove worktree."""
    add_pr_reviewer(g, owner, repo, pr_number, reviewer)
    post_comment(g, owner, repo, number, "ready for review — CI green")
    remove_worktree(owner, repo, number)


def run_manual_verify(g, owner: str, repo: str, number: int, pr_number: int, reviewer: str) -> None:
    """Add manual-verify label, assign reviewer, post comment, remove worktree."""
    repo_obj = g.get_repo(f"{owner}/{repo}")
    repo_obj.get_issue(number).add_to_labels("manual-verify-required")
    add_pr_reviewer(g, owner, repo, pr_number, reviewer)
    post_comment(g, owner, repo, number, "CI still red after auto-fixes — needs your hands")
    remove_worktree(owner, repo, number)
