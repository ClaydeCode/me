"""Safety gates — content filtering and plan approval checks.

Instead of gatekeeping which issues to work on, we filter *content* so the
LLM only sees comments/issue bodies that are created by or approved (👍)
by a whitelisted user.  Every assigned issue is a candidate, but if all
visible content is filtered out the issue is skipped.
"""

from github import Github

from clayde.config import get_settings


# ---------------------------------------------------------------------------
# Content filtering
# ---------------------------------------------------------------------------

def is_comment_visible(comment) -> bool:
    """Return True if a comment was created by or 👍'd by a whitelisted user."""
    whitelist = get_settings().whitelisted_users_list
    if comment.user.login in whitelist:
        return True
    return _has_whitelisted_reaction(comment.get_reactions())


def filter_comments(comments: list) -> list:
    """Return only comments that are visible (created/approved by a whitelisted user)."""
    return [c for c in comments if is_comment_visible(c)]


def is_issue_visible(issue) -> bool:
    """Return True if the issue was created by or 👍'd by a whitelisted user.

    This checks the issue *body* visibility — whether the LLM should see the
    issue body text.
    """
    whitelist = get_settings().whitelisted_users_list
    if issue.user.login in whitelist:
        return True
    return _has_whitelisted_reaction(issue.get_reactions())


def has_visible_content(issue, comments: list) -> bool:
    """Return True if there is any visible content (issue body or comments).

    An issue with no visible content at all should not be worked on.
    """
    if is_issue_visible(issue):
        return True
    if filter_comments(comments):
        return True
    return False


# ---------------------------------------------------------------------------
# Plan approval
# ---------------------------------------------------------------------------

def is_plan_approved(g: Github, owner: str, repo: str, number: int, comment_id: int) -> bool:
    """Return True if a whitelisted user reacted +1 to the plan comment."""
    comment = g.get_repo(f"{owner}/{repo}").get_issue(number).get_comment(comment_id)
    return _has_whitelisted_reaction(comment.get_reactions())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _has_whitelisted_reaction(reactions) -> bool:
    return any(
        r.content == "+1" and r.user.login in get_settings().whitelisted_users_list
        for r in reactions
    )
