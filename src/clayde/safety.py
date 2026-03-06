"""Safety gates — authorization checks before planning or implementing."""

from github import Github

from clayde.config import get_settings


def is_issue_authorized(issue) -> bool:
    """Return True if the issue author is whitelisted OR a whitelisted user reacted +1."""
    if issue.user.login in get_settings().whitelisted_users_list:
        return True
    return _has_whitelisted_reaction(issue.get_reactions())


def is_plan_approved(g: Github, owner: str, repo: str, number: int, comment_id: int) -> bool:
    """Return True if a whitelisted user reacted +1 to the plan comment."""
    comment = g.get_repo(f"{owner}/{repo}").get_issue(number).get_comment(comment_id)
    return _has_whitelisted_reaction(comment.get_reactions())


def _has_whitelisted_reaction(reactions) -> bool:
    return any(
        r.content == "+1" and r.user.login in get_settings().whitelisted_users_list
        for r in reactions
    )
