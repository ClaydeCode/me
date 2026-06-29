"""Per-issue git worktrees off a shared base clone."""
import logging
import subprocess
import threading
from pathlib import Path

from clayde.config import DATA_DIR
from clayde.git import ensure_repo

log = logging.getLogger("clayde.freeshard.worktree")

WORKTREES_DIR = DATA_DIR / "worktrees"

# Serialises concurrent git worktree add + ensure_repo calls on the shared base clone.
_worktree_lock = threading.Lock()


def _branch(number: int) -> str:
    return f"clayde/issue-{number}"


def add_worktree(owner: str, repo: str, number: int, default_branch: str) -> Path:
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    wt = WORKTREES_DIR / f"{owner}__{repo}__issue-{number}"
    branch = _branch(number)

    if (wt / ".git").exists():
        # reuse existing worktree, fetch the branch ref without merging to preserve local work
        subprocess.run(["git", "fetch", "origin", branch], cwd=str(wt),
                       capture_output=True, text=True)
        subprocess.run(["git", "checkout", branch], cwd=str(wt), capture_output=True, text=True)
        return wt

    # New worktree: lock around ensure_repo + git worktree add to prevent concurrent races
    # on the shared base clone's .git/worktrees index.
    with _worktree_lock:
        base = ensure_repo(owner, repo, default_branch)
        ls = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", branch],
            cwd=str(base), capture_output=True, text=True,
        )
        if ls.returncode != 0:
            raise RuntimeError(f"ls-remote failed for {branch}: {ls.stderr}")
        remote_has = ls.stdout.strip()
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
