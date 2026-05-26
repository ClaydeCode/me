"""Safety gates — content filtering.

Instead of gatekeeping which issues to work on, we filter *content* so the
LLM only sees comments/issue bodies that are created by or approved (👍)
by a whitelisted user.  Every assigned issue is a candidate, but if all
visible content is filtered out the issue is skipped.
"""

from datetime import datetime

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


def get_new_visible_comments(comments: list, last_seen_at: datetime | None) -> list:
    """Return visible comments newer than last_seen_at, excluding Clayde's own.

    If last_seen_at is None (first time or migrated state), returns all
    visible non-Clayde comments. Uses datetime comparison against
    comment.created_at.
    """
    github_username = get_settings().github_username
    visible = filter_comments(comments)
    if last_seen_at is None:
        return [c for c in visible if c.user.login != github_username]
    return [
        c for c in visible
        if c.created_at > last_seen_at and c.user.login != github_username
    ]


def filter_pr_reviews(reviews: list, github_username: str) -> list:
    """Return only PR reviews from whitelisted users, excluding the bot's own.

    A review is visible if the reviewer is in the whitelist and is not the
    authenticated bot account.
    """
    whitelist = get_settings().whitelisted_users_list
    return [
        r for r in reviews
        if r.user.login in whitelist and r.user.login != github_username
    ]


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
# Internal helpers
# ---------------------------------------------------------------------------

def _has_whitelisted_reaction(reactions) -> bool:
    return any(
        r.content == "+1" and r.user.login in get_settings().whitelisted_users_list
        for r in reactions
    )
