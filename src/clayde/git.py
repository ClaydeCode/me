"""Git repository management — clone or update repos under REPOS_DIR."""

import logging
import subprocess
from pathlib import Path

from clayde.config import DATA_DIR

log = logging.getLogger("clayde.git")

_REPOS_DIR = DATA_DIR / "repos"


def ensure_repo(owner: str, repo: str, default_branch: str) -> Path:
    """Clone repo if needed, otherwise checkout default_branch and pull.

    Returns the local path to the repository.
    """
    repos_dir = _REPOS_DIR
    repo_path = repos_dir / f"{owner}__{repo}"
    clone_url = f"https://github.com/{owner}/{repo}.git"

    if (repo_path / ".git").is_dir():
        log.info("Updating %s/%s (checkout %s + pull)", owner, repo, default_branch)
        subprocess.run(
            ["git", "checkout", default_branch],
            cwd=repo_path, capture_output=True,
        )
        subprocess.run(["git", "pull"], cwd=repo_path, capture_output=True)
    else:
        log.info("Cloning %s/%s", owner, repo)
        repos_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", clone_url, str(repo_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Clone failed: {result.stderr}")

    return repo_path
