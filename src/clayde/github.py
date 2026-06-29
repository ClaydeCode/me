"""GitHub API helpers using PyGitHub."""

import logging
import re

import requests
from github import Github, GithubException
from github.Issue import Issue
from github.IssueComment import IssueComment

from clayde.config import get_settings

log = logging.getLogger("clayde.github")

_FAILED_STATES = frozenset({"failure", "error", "cancelled", "timed_out", "action_required"})
_PENDING_STATES = frozenset({"pending", "queued", "in_progress"})


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
    """Return all open issues AND PRs assigned to the authenticated user.

    GitHub models PRs as issues — each item that is a PR will have
    ``html_url`` containing ``/pull/``.  Use ``is_pull_request_item()`` to
    distinguish them.
    """
    try:
        return list(g.get_user().get_issues(filter="assigned", state="open"))
    except GithubException as e:
        log.error("Failed to fetch assigned issues: %s", e)
        return []


def is_pull_request_item(item) -> bool:
    """Return True if an item from get_assigned_issues() is a pull request."""
    return "/pull/" in item.html_url


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


def get_pull(g: Github, owner: str, repo: str, pr_number: int):
    """Return the PullRequest object for the given PR number."""
    return _get_repo(g, owner, repo).get_pull(pr_number)


# ---------------------------------------------------------------------------
# Freeshard loop observation helpers
# ---------------------------------------------------------------------------

def get_ci_status(g: Github, owner: str, repo: str, ref: str) -> str | None:
    """Combined CI conclusion for a ref: success/failure/pending, or None if no checks.

    Merges the legacy commit-status API and check-runs: failure dominates,
    then pending, else success. None means nothing has reported yet.
    """
    commit = _get_repo(g, owner, repo).get_commit(ref)
    states: list[str] = []

    # Legacy commit-status API defaults state to "pending" when a repo has zero
    # statuses (modern repos use check-runs only). Only trust it when statuses
    # actually exist — otherwise it poisons a green check-runs result to pending.
    combined_status = commit.get_combined_status()
    if combined_status.total_count > 0:
        combined = combined_status.state  # success|failure|pending|error
        if combined in ("failure", "error"):
            states.append("failure")
        elif combined == "pending":
            states.append("pending")
        else:
            states.append("success")

    for run in commit.get_check_runs():
        if run.status != "completed":
            states.append("pending")
        else:
            states.append(run.conclusion or "failure")

    if not states:
        return None
    if not _FAILED_STATES.isdisjoint(states):
        return "failure"
    if not _PENDING_STATES.isdisjoint(states):
        return "pending"
    return "success"


def is_reviewer_assigned(g: Github, owner: str, repo: str, pr_number: int, login: str) -> bool:
    """Return True if login has been requested as reviewer or has submitted a review."""
    pr = _get_repo(g, owner, repo).get_pull(pr_number)
    requested_users, _ = pr.get_review_requests()
    if any(u.login.lower() == login.lower() for u in requested_users):
        return True
    return any(r.user and r.user.login.lower() == login.lower() for r in pr.get_reviews())


def count_fix_commits(g: Github, owner: str, repo: str, branch: str, default_branch: str) -> int:
    """Return number of commits unique to branch whose message starts with 'fix(ci):'.

    Uses the compare API so only branch-unique commits are counted — base-branch
    history containing 'fix(ci):' commits cannot inflate fix_attempts.
    """
    commits = _get_repo(g, owner, repo).compare(default_branch, branch).commits
    return sum(1 for c in commits if c.commit.message.startswith("fix(ci):"))


def count_branch_commits(g: Github, owner: str, repo: str, branch: str, default_branch: str) -> int:
    """Return total number of commits unique to branch vs default_branch.

    Uses the compare API so only branch-unique commits are counted. Returns 0
    on GithubException (branch missing, branch equal to base, etc.).
    """
    try:
        return len(_get_repo(g, owner, repo).compare(default_branch, branch).commits)
    except GithubException:
        return 0


def has_ci_workflows(g: Github, owner: str, repo: str) -> bool:
    """Return True if the repo has any file under .github/workflows/.

    Uses the contents API. Returns False on 404 or any GithubException
    (repo has no workflows directory). Defaults to True on other errors
    (safer to wait for CI than to hand off prematurely).
    """
    try:
        contents = _get_repo(g, owner, repo).get_contents(".github/workflows")
        return bool(contents)
    except GithubException:
        return False
