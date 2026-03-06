"""Git repository management — clone or update repos under REPOS_DIR."""

import logging
import os
import subprocess

from clayde.config import get_settings

log = logging.getLogger("clayde.git")


def ensure_repo(owner: str, repo: str, default_branch: str) -> str:
    """Clone repo if needed, otherwise checkout default_branch and pull.

    Returns the local path to the repository.
    """
    repos_dir = get_settings().repos_dir
    repo_path = os.path.join(repos_dir, f"{owner}__{repo}")
    clone_url = f"https://github.com/{owner}/{repo}.git"

    if os.path.isdir(os.path.join(repo_path, ".git")):
        log.info("Updating %s/%s (checkout %s + pull)", owner, repo, default_branch)
        subprocess.run(
            ["git", "checkout", default_branch],
            cwd=repo_path, capture_output=True,
        )
        subprocess.run(["git", "pull"], cwd=repo_path, capture_output=True)
    else:
        log.info("Cloning %s/%s", owner, repo)
        os.makedirs(repos_dir, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", clone_url, repo_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Clone failed: {result.stderr}")

    return repo_path
