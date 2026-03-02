"""GitHub CLI wrappers and repo management."""

import json
import logging
import os
import re
import subprocess

from clayde.config import APPROVER, REPOS_DIR, WHITELISTED_USERS

log = logging.getLogger("clayde.github")


def gh_api(endpoint, method="GET", fields=None):
    """Call gh api and return parsed JSON."""
    cmd = ["gh", "api", endpoint]
    if method != "GET":
        cmd += ["--method", method]
    for k, v in (fields or {}).items():
        cmd += ["-f", f"{k}={v}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api {endpoint} failed: {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def parse_issue_url(url):
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", url)
    if not m:
        raise ValueError(f"Cannot parse issue URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_issue(owner, repo, number):
    return gh_api(f"/repos/{owner}/{repo}/issues/{number}")


def fetch_issue_comments(owner, repo, number):
    return gh_api(f"/repos/{owner}/{repo}/issues/{number}/comments")


def post_comment(owner, repo, number, body):
    data = gh_api(
        f"/repos/{owner}/{repo}/issues/{number}/comments",
        method="POST",
        fields={"body": body},
    )
    return data["id"]


def get_default_branch(owner, repo):
    data = gh_api(f"/repos/{owner}/{repo}")
    return data.get("default_branch", "main")


def ensure_repo(owner, repo):
    """Clone or update a repository under REPOS_DIR."""
    repo_path = os.path.join(REPOS_DIR, f"{owner}__{repo}")
    clone_url = f"https://github.com/{owner}/{repo}.git"

    if os.path.isdir(os.path.join(repo_path, ".git")):
        default_branch = get_default_branch(owner, repo)
        log.info("Updating %s/%s (checkout %s + pull)", owner, repo, default_branch)
        subprocess.run(
            ["git", "checkout", default_branch],
            cwd=repo_path, capture_output=True,
        )
        subprocess.run(["git", "pull"], cwd=repo_path, capture_output=True)
    else:
        log.info("Cloning %s/%s", owner, repo)
        os.makedirs(REPOS_DIR, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", clone_url, repo_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Clone failed: {result.stderr}")

    return repo_path


def get_assigned_issues():
    """Fetch all open issues assigned to the authenticated user."""
    try:
        return gh_api("/issues?filter=assigned&state=open&per_page=100")
    except RuntimeError as e:
        log.error("Failed to fetch assigned issues: %s", e)
        return []


def check_approval(owner, repo, comment_id):
    """Check if APPROVER has reacted with thumbs-up to the plan comment."""
    try:
        reactions = gh_api(
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"
        )
    except RuntimeError:
        return False
    return any(
        r.get("content") == "+1" and r.get("user", {}).get("login") == APPROVER
        for r in reactions
    )


def is_whitelisted_author(issue):
    """Check if the issue was created by a whitelisted user."""
    author = issue.get("user", {}).get("login", "")
    return author in WHITELISTED_USERS


def has_whitelisted_thumbsup(owner, repo, number):
    """Check if any whitelisted user has reacted with +1 to the issue itself."""
    try:
        reactions = gh_api(
            f"/repos/{owner}/{repo}/issues/{number}/reactions"
        )
    except RuntimeError:
        return False
    return any(
        r.get("content") == "+1" and r.get("user", {}).get("login") in WHITELISTED_USERS
        for r in reactions
    )
