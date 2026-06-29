"""Pre-PR local verification for Freeshard issues."""
import subprocess
from pathlib import Path


def local_verify(profile: str, worktree: Path) -> tuple[bool, str]:
    """Run the repo's pre-PR verification. Returns (ok, log_tail)."""
    if profile == "none":
        return (True, "")

    if profile == "needs-shard":
        raise NotImplementedError("needs-shard verify lands in Task 10 (compose substrate)")

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
