"""Async Pebble invocation of the Claude CLI — fresh session per call."""

from __future__ import annotations

import asyncio
import json
import logging

from clayde.claude import (
    InvocationTimeoutError,
    UsageLimitError,
    _is_auth_error,
    _is_limit_error,
    _make_cli_env,
    _resolve_cli_bin,
)

log = logging.getLogger("clayde.webhook.worker")


async def invoke_claude_pebble(
    *, system_prompt: str, user_text: str, cwd: str, timeout_s: int,
) -> str:
    """Run the Claude CLI for a single Pebble request and return its result text.

    Always a fresh session — no resume, no session-id persistence.
    Raises ``InvocationTimeoutError`` on timeout, ``UsageLimitError`` on
    rate/usage limits, ``RuntimeError`` on auth errors.
    """
    cli_bin = _resolve_cli_bin()
    cmd = [
        cli_bin,
        "-p", user_text,
        "--append-system-prompt", system_prompt,
        "--output-format", "json",
        "--dangerously-skip-permissions",
    ]
    log.info("Invoking Claude CLI (cwd=%s, timeout=%ds)", cwd, timeout_s)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=_make_cli_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s,
        )
    except asyncio.TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise InvocationTimeoutError(
            f"Claude CLI timed out after {timeout_s}s"
        ) from e

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    output_text = ""
    is_error = False
    try:
        parsed = json.loads(stdout)
        output_text = parsed.get("result", "") or ""
        is_error = bool(parsed.get("is_error", False))
    except (json.JSONDecodeError, TypeError):
        output_text = stdout

    if proc.returncode != 0 or is_error:
        error_text = stderr
        if is_error:
            error_text += " " + output_text
        if _is_limit_error(error_text):
            raise UsageLimitError("Claude CLI usage limit hit")
        if _is_auth_error(error_text):
            raise RuntimeError("Claude CLI authentication failed")
        log.error(
            "Claude CLI exited rc=%d is_error=%s stderr=%s",
            proc.returncode, is_error, stderr[:500],
        )

    return output_text
