"""Claude CLI invocation."""

import logging
import os
import subprocess
from pathlib import Path

from clayde.config import get_settings
from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.claude")

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))

_LIMIT_PATTERNS = [
    "usage limit",
    "rate limit",
    "session limit",
    "claude code pro",
    "you've reached",
    "exceeded your",
    "hit your limit",
    "resets at",
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
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.invoke_claude") as span:
        identity = (get_settings().dir / "CLAUDE.md").read_text()

        try:
            result = subprocess.run(
                [
                    CLAUDE_BIN, "-p", prompt,
                    "--append-system-prompt", identity,
                    "--dangerously-skip-permissions",
                ],
                cwd=repo_path,
                env=_make_env(),
                text=True,
                capture_output=True,
                timeout=1800,
            )
        except subprocess.TimeoutExpired as e:
            span.set_attribute("claude.timeout", True)
            span.record_exception(e)
            raise

        span.set_attribute("claude.timeout", False)
        span.set_attribute("claude.exit_code", result.returncode)
        span.set_attribute("claude.output_length", len(result.stdout or ""))

        combined = (result.stdout or "") + (result.stderr or "")

        if result.returncode != 0:
            log.error("Claude exited with code %d", result.returncode)
            if result.stderr:
                log.error("stderr: %s", result.stderr[:500])
            if _is_limit_error(combined):
                span.set_attribute("claude.usage_limit", True)
                exc = UsageLimitError("Claude usage limit hit")
                span.record_exception(exc)
                raise exc

        # Claude sometimes exits 0 but embeds a limit message in stdout
        if _is_limit_error(result.stdout or ""):
            span.set_attribute("claude.usage_limit", True)
            exc = UsageLimitError("Claude usage limit hit (exit 0 but limit message in stdout)")
            span.record_exception(exc)
            raise exc

        span.set_attribute("claude.usage_limit", False)
        return result.stdout or ""


def is_claude_available():
    """Return True if Claude is available (usage limit not currently hit).

    Runs a minimal invocation; only returns False when a limit pattern is
    detected. Any other error (CLI not found, unexpected failure) is treated
    as available so we don't suppress real work on spurious pre-check errors.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.claude_available_check") as span:
        try:
            result = subprocess.run(
                [CLAUDE_BIN, "-p", "respond with: OK"],
                env=_make_env(),
                text=True,
                capture_output=True,
                timeout=60,
            )
            combined = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0 and _is_limit_error(combined):
                span.set_attribute("claude.available", False)
                return False
            if _is_limit_error(result.stdout or ""):
                span.set_attribute("claude.available", False)
                return False
            span.set_attribute("claude.available", True)
            return True
        except Exception as exc:
            log.warning("Claude availability pre-check raised %s — assuming available", exc)
            span.set_attribute("claude.available", True)
            span.set_attribute("claude.check_error", str(exc))
            return True
