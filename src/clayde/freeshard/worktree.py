"""Per-issue git worktrees off a shared base clone."""
import logging
import subprocess
from pathlib import Path

from clayde.config import DATA_DIR
from clayde.git import ensure_repo

log = logging.getLogger("clayde.freeshard.worktree")

WORKTREES_DIR = DATA_DIR / "worktrees"


def _branch(number: int) -> str:
    return f"clayde/issue-{number}"


def add_worktree(owner: str, repo: str, number: int, default_branch: str) -> Path:
    base = ensure_repo(owner, repo, default_branch)  # base clone, fresh default branch
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    wt = WORKTREES_DIR / f"{owner}__{repo}__issue-{number}"
    branch = _branch(number)

    if (wt / ".git").exists():
        # reuse existing worktree, pull any remote progress
        subprocess.run(["git", "fetch", "origin", branch], cwd=str(wt),
                       capture_output=True, text=True)
        subprocess.run(["git", "checkout", branch], cwd=str(wt), capture_output=True, text=True)
        return wt

    # create worktree on a new or existing branch
    remote_has = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=str(base), capture_output=True, text=True,
    ).stdout.strip()
    if remote_has:
        args = ["git", "worktree", "add", "-B", branch, str(wt), f"origin/{branch}"]
    else:
        args = ["git", "worktree", "add", "-b", branch, str(wt), default_branch]
    res = subprocess.run(args, cwd=str(base), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"worktree add failed: {res.stderr}")
    return wt


def remove_worktree(owner: str, repo: str, number: int) -> None:
    base = DATA_DIR / "repos" / f"{owner}__{repo}"
    wt = WORKTREES_DIR / f"{owner}__{repo}__issue-{number}"
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                   cwd=str(base), capture_output=True, text=True)
