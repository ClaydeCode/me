"""GitHub API helpers using PyGitHub."""

import logging
import re

from github import Github, GithubException

log = logging.getLogger("clayde.github")


def parse_issue_url(url: str) -> tuple[str, str, int]:
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", url)
    if not m:
        raise ValueError(f"Cannot parse issue URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_issue(g: Github, owner: str, repo: str, number: int):
    return g.get_repo(f"{owner}/{repo}").get_issue(number)


def fetch_issue_comments(g: Github, owner: str, repo: str, number: int):
    return list(g.get_repo(f"{owner}/{repo}").get_issue(number).get_comments())


def post_comment(g: Github, owner: str, repo: str, number: int, body: str) -> int:
    """Post a comment on an issue and return the comment ID."""
    comment = g.get_repo(f"{owner}/{repo}").get_issue(number).create_comment(body)
    return comment.id


def fetch_comment(g: Github, owner: str, repo: str, number: int, comment_id: int):
    return g.get_repo(f"{owner}/{repo}").get_issue(number).get_comment(comment_id)


def get_default_branch(g: Github, owner: str, repo: str) -> str:
    return g.get_repo(f"{owner}/{repo}").default_branch


def get_assigned_issues(g: Github) -> list:
    """Return all open issues assigned to the authenticated user."""
    try:
        return list(g.get_user().get_issues(filter="assigned", state="open"))
    except GithubException as e:
        log.error("Failed to fetch assigned issues: %s", e)
        return []


def find_open_pr(g: Github, owner: str, repo: str, number: int) -> str | None:
    """Return the HTML URL of an open PR for clayde/issue-{number}, or None."""
    branch = f"clayde/issue-{number}"
    pulls = list(g.get_repo(f"{owner}/{repo}").get_pulls(
        state="open", head=f"{owner}:{branch}"
    ))
    return pulls[0].html_url if pulls else None
