"""Pre-PR local verification for Freeshard issues."""
import subprocess
from pathlib import Path

# Per-repo shard config for needs-shard profile.
# Populated when a repo is added to _NEEDS_SHARD (Phase 2 / freeshard compose spike).
# Each entry: {"compose_file": str, "command": list[str]}
_SHARD_CONFIG: dict[str, dict] = {}


def local_verify(
    profile: str,
    worktree: Path,
    repo: str | None = None,
) -> tuple[bool, str]:
    """Run the repo's pre-PR verification. Returns (ok, log_tail)."""
    if profile == "none":
        return (True, "")

    if profile == "needs-shard":
        if repo is None or repo not in _SHARD_CONFIG:
            raise RuntimeError(
                f"needs-shard verify not configured for repo {repo!r} — "
                "requires the freeshard docker-compose spike (Phase 2 deferral)"
            )
        cfg = _SHARD_CONFIG[repo]
        return _verify_with_shard(worktree, cfg["compose_file"], cfg["command"])

    # profile == "tests-only": discover and run test runner
    if (worktree / "justfile").exists() or (worktree / "Justfile").exists():
        cmd = ["just", "test"]
    elif (worktree / "package.json").exists():
        cmd = ["npm", "test"]
    elif (worktree / "pyproject.toml").exists():
        cmd = ["uv", "run", "pytest"]
    else:
        return (True, "")

    result = subprocess.run(
        cmd, cwd=str(worktree), text=True, capture_output=True,
    )
    combined = result.stdout + result.stderr
    tail = combined[-2000:] if len(combined) > 2000 else combined
    return (result.returncode == 0, tail)


def _verify_with_shard(
    worktree: Path,
    compose_file: str,
    command: list[str],
) -> tuple[bool, str]:
    """Bring up shard, run command, always tear down. Returns (ok, log_tail)."""
    try:
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "up", "-d"],
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            command, cwd=str(worktree), text=True, capture_output=True,
        )
        combined = result.stdout + result.stderr
        tail = combined[-2000:] if len(combined) > 2000 else combined
        return (result.returncode == 0, tail)
    finally:
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "down", "-v"],
            capture_output=True,
        )
