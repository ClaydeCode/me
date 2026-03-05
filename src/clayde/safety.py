"""Safety gates — authorization checks before planning or implementing."""

from github import Github

from clayde.config import APPROVER, WHITELISTED_USERS


def is_issue_authorized(issue) -> bool:
    """Return True if the issue author is whitelisted OR a whitelisted user reacted +1."""
    if issue.user.login in WHITELISTED_USERS:
        return True
    return _has_whitelisted_reaction(issue.get_reactions())


def is_plan_approved(g: Github, owner: str, repo: str, number: int, comment_id: int) -> bool:
    """Return True if APPROVER reacted +1 to the plan comment AND a whitelisted user reacted +1 to the issue."""
    repo_obj = g.get_repo(f"{owner}/{repo}")
    issue = repo_obj.get_issue(number)
    comment = repo_obj.get_issue_comment(comment_id)
    approver_approved = any(
        r.content == "+1" and r.user.login == APPROVER
        for r in comment.get_reactions()
    )
    return approver_approved and _has_whitelisted_reaction(issue.get_reactions())


def _has_whitelisted_reaction(reactions) -> bool:
    return any(
        r.content == "+1" and r.user.login in WHITELISTED_USERS
        for r in reactions
    )
