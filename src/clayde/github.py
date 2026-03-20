"""GitHub API helpers using PyGitHub."""

import logging
import re

import requests
from github import Github, GithubException
from github.Issue import Issue
from github.IssueComment import IssueComment

from clayde.config import get_settings

log = logging.getLogger("clayde.github")


def _get_repo(g: Github, owner: str, repo: str):
    return g.get_repo(f"{owner}/{repo}")


def issue_ref(owner: str, repo: str, number: int) -> str:
    """Return 'owner/repo#number' for use in log lines and status output."""
    return f"{owner}/{repo}#{number}"


def parse_issue_url(url: str) -> tuple[str, str, int]:
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", url)
    if not m:
        raise ValueError(f"Cannot parse issue URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_issue(g: Github, owner: str, repo: str, number: int) -> Issue:
    return _get_repo(g, owner, repo).get_issue(number)


def fetch_issue_comments(g: Github, owner: str, repo: str, number: int) -> list[IssueComment]:
    return list(_get_repo(g, owner, repo).get_issue(number).get_comments())


def post_comment(g: Github, owner: str, repo: str, number: int, body: str) -> int:
    """Post a comment on an issue and return the comment ID."""
    comment = _get_repo(g, owner, repo).get_issue(number).create_comment(body)
    return comment.id


def edit_comment(g: Github, owner: str, repo: str, number: int, comment_id: int, body: str) -> None:
    """Edit an existing issue comment."""
    _get_repo(g, owner, repo).get_issue(number).get_comment(comment_id).edit(body)


def fetch_comment(g: Github, owner: str, repo: str, number: int, comment_id: int) -> IssueComment:
    return _get_repo(g, owner, repo).get_issue(number).get_comment(comment_id)


def get_default_branch(g: Github, owner: str, repo: str) -> str:
    return _get_repo(g, owner, repo).default_branch


def get_assigned_issues(g: Github) -> list:
    """Return all open issues assigned to the authenticated user."""
    try:
        return list(g.get_user().get_issues(filter="assigned", state="open"))
    except GithubException as e:
        log.error("Failed to fetch assigned issues: %s", e)
        return []


def find_open_pr(g: Github, owner: str, repo: str, branch_name: str) -> str | None:
    """Return the HTML URL of an open PR for the given branch, or None."""
    pulls = list(_get_repo(g, owner, repo).get_pulls(
        state="open", head=f"{owner}:{branch_name}"
    ))
    return pulls[0].html_url if pulls else None


def create_pull_request(
    g: Github, owner: str, repo: str, *,
    title: str, body: str, head: str, base: str,
) -> str:
    """Create a pull request and return its HTML URL."""
    pr = _get_repo(g, owner, repo).create_pull(
        title=title, body=body, head=head, base=base,
    )
    return pr.html_url


# ---------------------------------------------------------------------------
# Blocked-issue detection via GitHub sub-issue relationships
# ---------------------------------------------------------------------------

def is_blocked(g: Github, owner: str, repo: str, number: int) -> bool:
    """Return True if this issue is a sub-issue of an open parent issue.

    Uses the GitHub timeline API to detect formal 'connected' events that
    GitHub creates when one issue is added as a sub-issue of another.
    """
    try:
        token = get_settings().github_token
        if token:
            return _has_open_parent_issue(token, owner, repo, number)
    except Exception as e:
        log.warning("Failed to check sub-issue relationships for %s/%s#%d: %s",
                    owner, repo, number, e)
    return False


def _fetch_timeline_events(token: str, owner: str, repo: str, number: int) -> list:
    """Return timeline events for an issue from the GitHub REST API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/timeline"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def _has_open_parent_issue(token: str, owner: str, repo: str, number: int) -> bool:
    """Return True if this issue has a 'connected' parent issue that is still open."""
    try:
        events = _fetch_timeline_events(token, owner, repo, number)
    except Exception as e:
        log.warning("Timeline API request failed for %s/%s#%d: %s", owner, repo, number, e)
        return False

    for event in events:
        if event.get("event") != "connected":
            continue
        source_issue = event.get("source", {}).get("issue", {})
        if source_issue.get("state") == "open":
            source_url = source_issue.get("html_url", "unknown")
            log.info("Issue %s/%s#%d is a sub-issue of open parent %s",
                     owner, repo, number, source_url)
            return True
    return False


# ---------------------------------------------------------------------------
# PR review helpers
# ---------------------------------------------------------------------------

def add_pr_reviewer(g: Github, owner: str, repo: str, pr_number: int, reviewer_login: str) -> None:
    """Request a review from the specified user on a PR."""
    try:
        pr = _get_repo(g, owner, repo).get_pull(pr_number)
        pr.create_review_request(reviewers=[reviewer_login])
        log.info("Requested review from %s on PR #%d", reviewer_login, pr_number)
    except GithubException as e:
        log.warning("Failed to add reviewer %s to PR #%d: %s", reviewer_login, pr_number, e)


def get_pr_reviews(g: Github, owner: str, repo: str, pr_number: int) -> list:
    """Return all reviews on a PR."""
    return list(_get_repo(g, owner, repo).get_pull(pr_number).get_reviews())


def get_pr_review_comments(g: Github, owner: str, repo: str, pr_number: int) -> list:
    """Return all review comments (inline) on a PR."""
    return list(_get_repo(g, owner, repo).get_pull(pr_number).get_review_comments())


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a PR URL into (owner, repo, pr_number)."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise ValueError(f"Cannot parse PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def get_issue_author(g: Github, owner: str, repo: str, number: int) -> str:
    """Return the login of the issue author."""
    issue = _get_repo(g, owner, repo).get_issue(number)
    return issue.user.login


def get_pr_title(g: Github, owner: str, repo: str, pr_number: int) -> str:
    """Return the title of a pull request."""
    return _get_repo(g, owner, repo).get_pull(pr_number).title
