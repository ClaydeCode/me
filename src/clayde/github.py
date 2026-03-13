"""GitHub API helpers using PyGitHub."""

import logging
import re

import requests
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


def edit_comment(g: Github, owner: str, repo: str, number: int, comment_id: int, body: str) -> None:
    """Edit an existing issue comment."""
    g.get_repo(f"{owner}/{repo}").get_issue(number).get_comment(comment_id).edit(body)


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


def extract_branch_name(plan_text: str, number: int) -> str:
    """Extract branch name from plan text, falling back to clayde/issue-{number}."""
    m = re.search(r"\*\*Branch:\*\*\s*`(clayde/issue-\d+-[a-z0-9-]+)`", plan_text)
    if m:
        return m.group(1)
    return f"clayde/issue-{number}"


def find_open_pr(g: Github, owner: str, repo: str, branch_name: str) -> str | None:
    """Return the HTML URL of an open PR for the given branch, or None."""
    pulls = list(g.get_repo(f"{owner}/{repo}").get_pulls(
        state="open", head=f"{owner}:{branch_name}"
    ))
    return pulls[0].html_url if pulls else None


def create_pull_request(
    g: Github, owner: str, repo: str, *,
    title: str, body: str, head: str, base: str,
) -> str:
    """Create a pull request and return its HTML URL."""
    pr = g.get_repo(f"{owner}/{repo}").create_pull(
        title=title, body=body, head=head, base=base,
    )
    return pr.html_url


# ---------------------------------------------------------------------------
# Blocked-issue detection via GitHub sub-issue relationships
# ---------------------------------------------------------------------------

def is_blocked(g: Github, owner: str, repo: str, number: int) -> bool:
    """Return True if an issue is blocked by another open issue.

    Uses the GitHub timeline API to find cross-reference events that
    represent explicit sub-issue / tracked-by relationships. An issue
    is blocked if any of its parent/blocking issues are still open.

    Also parses the issue body for "blocked by #N" / "depends on #N" text
    patterns as a fallback.
    """
    issue = g.get_repo(f"{owner}/{repo}").get_issue(number)

    # Check body text for blocking patterns
    if issue.body:
        if _has_blocking_references(g, owner, repo, issue.body):
            return True

    # Check explicit sub-issue relationships via timeline events
    try:
        token = g.auth.token if hasattr(g.auth, "token") else None
        if token:
            if _has_blocking_sub_issue_parents(token, owner, repo, number):
                return True
    except Exception as e:
        log.warning("Failed to check sub-issue relationships for %s/%s#%d: %s",
                    owner, repo, number, e)

    return False


def _has_blocking_references(g: Github, owner: str, repo: str, body: str) -> bool:
    """Check issue body for 'blocked by #N' / 'depends on #N' patterns.

    Supports both same-repo (#N) and cross-repo (owner/repo#N) references.
    """
    # Patterns: "blocked by #123", "depends on #45", "blocked by owner/repo#67"
    patterns = [
        r"(?:blocked\s+by|depends\s+on)\s+#(\d+)",
        r"(?:blocked\s+by|depends\s+on)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)",
    ]

    # Same-repo references
    for m in re.finditer(patterns[0], body, re.IGNORECASE):
        ref_number = int(m.group(1))
        try:
            ref_issue = g.get_repo(f"{owner}/{repo}").get_issue(ref_number)
            if ref_issue.state == "open":
                log.info("Issue %s/%s#%d is blocked by #%d (open)", owner, repo,
                         ref_number, ref_number)
                return True
        except GithubException:
            pass

    # Cross-repo references
    for m in re.finditer(patterns[1], body, re.IGNORECASE):
        ref_repo_full = m.group(1)
        ref_number = int(m.group(2))
        try:
            ref_issue = g.get_repo(ref_repo_full).get_issue(ref_number)
            if ref_issue.state == "open":
                log.info("Issue %s/%s is blocked by %s#%d (open)", owner, repo,
                         ref_repo_full, ref_number)
                return True
        except GithubException:
            pass

    return False


def _has_blocking_sub_issue_parents(token: str, owner: str, repo: str, number: int) -> bool:
    """Check for explicit GitHub sub-issue parent relationships via timeline API.

    GitHub's sub-issues feature creates timeline events of type
    'sub_issue_added' / 'sub_issue_removed' on the parent issue, and
    'cross-referenced' events with specific source types. We use the
    REST timeline API to detect these.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/timeline"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        events = response.json()
    except Exception as e:
        log.warning("Timeline API request failed for %s/%s#%d: %s",
                    owner, repo, number, e)
        return False

    for event in events:
        event_type = event.get("event")

        # Check for "connected" / "cross-referenced" events that indicate
        # this issue is a sub-issue (child) of another issue
        if event_type == "cross-referenced":
            source = event.get("source", {})
            source_issue = source.get("issue", {})
            # If the source issue references this one as a sub-issue and
            # the source issue is still open, we're blocked
            if source_issue.get("state") == "open":
                # Check if the source issue's body contains a task-list
                # reference or sub-issue tracking for our issue
                source_body = source_issue.get("body") or ""
                # GitHub tracks sub-issues with task list items like
                # "- [ ] #N" or "- [ ] owner/repo#N"
                task_pattern = rf"- \[ \]\s+(?:https://github\.com/{owner}/{repo}/issues/{number}|{owner}/{repo}#{number}|#{number})"
                if re.search(task_pattern, source_body):
                    source_url = source_issue.get("html_url", "unknown")
                    log.info("Issue %s/%s#%d is blocked by parent issue %s (open)",
                             owner, repo, number, source_url)
                    return True

    return False


# ---------------------------------------------------------------------------
# PR review helpers
# ---------------------------------------------------------------------------

def add_pr_reviewer(g: Github, owner: str, repo: str, pr_number: int, reviewer_login: str) -> None:
    """Request a review from the specified user on a PR."""
    try:
        pr = g.get_repo(f"{owner}/{repo}").get_pull(pr_number)
        pr.create_review_request(reviewers=[reviewer_login])
        log.info("Requested review from %s on PR #%d", reviewer_login, pr_number)
    except GithubException as e:
        log.warning("Failed to add reviewer %s to PR #%d: %s", reviewer_login, pr_number, e)


def get_pr_reviews(g: Github, owner: str, repo: str, pr_number: int) -> list:
    """Return all reviews on a PR."""
    return list(g.get_repo(f"{owner}/{repo}").get_pull(pr_number).get_reviews())


def get_pr_review_comments(g: Github, owner: str, repo: str, pr_number: int) -> list:
    """Return all review comments (inline) on a PR."""
    return list(g.get_repo(f"{owner}/{repo}").get_pull(pr_number).get_review_comments())


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a PR URL into (owner, repo, pr_number)."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise ValueError(f"Cannot parse PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def get_issue_author(g: Github, owner: str, repo: str, number: int) -> str:
    """Return the login of the issue author."""
    issue = g.get_repo(f"{owner}/{repo}").get_issue(number)
    return issue.user.login
