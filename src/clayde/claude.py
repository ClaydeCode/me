"""Claude CLI invocation."""

import logging
import os
import subprocess

from clayde.config import CLAYDE_DIR

log = logging.getLogger("clayde.claude")

_LIMIT_PATTERNS = [
    "usage limit",
    "rate limit",
    "session limit",
    "claude code pro",
    "you've reached",
    "exceeded your",
]


class UsageLimitError(Exception):
    """Raised when Claude CLI reports a usage/rate limit."""


def _is_limit_error(text):
    t = text.lower()
    return any(p in t for p in _LIMIT_PATTERNS)


def _make_env():
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def invoke_claude(prompt, repo_path):
    """Run claude -p with the given prompt in repo_path.

    Raises UsageLimitError if the Claude CLI reports a usage/rate limit.
    """
    identity = open(os.path.join(CLAYDE_DIR, "CLAUDE.md")).read()

    result = subprocess.run(
        [
            os.path.expanduser("~/.local/bin/claude"), "-p", prompt,
            "--append-system-prompt", identity,
            "--dangerously-skip-permissions",
        ],
        cwd=repo_path,
        env=_make_env(),
        text=True,
        capture_output=True,
        timeout=1800,
    )

    combined = (result.stdout or "") + (result.stderr or "")

    if result.returncode != 0:
        log.error("Claude exited with code %d", result.returncode)
        if result.stderr:
            log.error("stderr: %s", result.stderr[:500])
        if _is_limit_error(combined):
            raise UsageLimitError("Claude usage limit hit")

    # Claude sometimes exits 0 but embeds a limit message in stdout
    if _is_limit_error(result.stdout or ""):
        raise UsageLimitError("Claude usage limit hit (exit 0 but limit message in stdout)")

    return result.stdout or ""


def is_claude_available():
    """Return True if Claude is available (usage limit not currently hit).

    Runs a minimal invocation; only returns False when a limit pattern is
    detected. Any other error (CLI not found, unexpected failure) is treated
    as available so we don't suppress real work on spurious pre-check errors.
    """
    try:
        result = subprocess.run(
            [os.path.expanduser("~/.local/bin/claude"), "-p", "respond with: OK"],
            env=_make_env(),
            text=True,
            capture_output=True,
            timeout=60,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and _is_limit_error(combined):
            return False
        if _is_limit_error(result.stdout or ""):
            return False
        return True
    except Exception as exc:
        log.warning("Claude availability pre-check raised %s — assuming available", exc)
        return True
