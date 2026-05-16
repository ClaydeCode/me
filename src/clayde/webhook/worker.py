"""Background worker: pop jobs, invoke the Claude CLI, emit ntfy on every outcome."""

from __future__ import annotations

import logging
import time

from clayde.claude import (
    CliInvocationError,
    InvocationTimeoutError,
    UsageLimitError,
)
from clayde.config import get_settings
from clayde.telemetry import get_tracer
from clayde.webhook.notify import send_ntfy
from clayde.webhook.queue import JobQueue, PebbleJob
from clayde.webhook.runner import extract_notification_payload, invoke_claude_pebble
from clayde.webhook.skills import (
    SKILLS_ROOT,
    build_system_prompt,
    build_user_prompt,
    discover_skills,
)

log = logging.getLogger("clayde.webhook.worker")

_FALLBACK_TITLE = "Pebble: done (no summary)"


def _tail(s: str, n: int) -> str:
    if not s:
        return ""
    return s[-n:]


async def _notify(*, title: str, body: str, success: bool) -> None:
    settings = get_settings()
    await send_ntfy(
        title=title,
        body=body,
        success=success,
        base_url=settings.ntfy_base_url,
        topic=settings.ntfy_topic,
        timeout_s=settings.ntfy_timeout_s,
    )


async def process_job(job: PebbleJob, *, timeout_s: int, kb_path: str) -> None:
    """Process a single Pebble job. Emits exactly one ntfy notification."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.pebble.process") as span:
        span.set_attribute("pebble.job_id", job.id)
        span.set_attribute("pebble.timestamp", job.timestamp)
        span.set_attribute("pebble.text", job.text)
        span.set_attribute("pebble.text_len", len(job.text))

        skills = discover_skills(SKILLS_ROOT)
        span.set_attribute("pebble.skills_available", len(skills))
        system_prompt = build_system_prompt(skills)
        user_text = build_user_prompt(job.text, job.timestamp)

        t0 = time.monotonic()
        outcome = "worker_error"
        try:
            output = await invoke_claude_pebble(
                system_prompt=system_prompt,
                user_text=user_text,
                cwd=kb_path,
                timeout_s=timeout_s,
            )
            payload = extract_notification_payload(output)
            if payload.title == _FALLBACK_TITLE:
                outcome = "parse_fallback"
            elif payload.success:
                outcome = "success"
            else:
                outcome = "claude_fail"
            await _notify(
                title=payload.title, body=payload.body, success=payload.success,
            )
            log.info("[%s] processed outcome=%s", job.id, outcome)
        except InvocationTimeoutError:
            outcome = "timeout"
            log.warning("[%s] timeout", job.id)
            await _notify(
                title="Pebble: timeout",
                body=f"ran {timeout_s}s+",
                success=False,
            )
        except UsageLimitError:
            outcome = "rate_limited"
            log.warning("[%s] usage limit hit", job.id)
            await _notify(
                title="Pebble: rate-limited",
                body="try again later",
                success=False,
            )
        except CliInvocationError as exc:
            outcome = "cli_error"
            log.error("[%s] CLI error: %s", job.id, exc.stderr[:200])
            await _notify(
                title="Pebble: failed",
                body=_tail(exc.stderr, 300) or "claude CLI exited non-zero",
                success=False,
            )
        except RuntimeError as exc:
            # Auth failures raise RuntimeError from the existing runner.
            outcome = "cli_error"
            log.error("[%s] auth error: %s", job.id, exc)
            await _notify(
                title="Pebble: auth error",
                body=str(exc)[:300],
                success=False,
            )
        except Exception as exc:
            outcome = "worker_error"
            log.exception("[%s] worker error", job.id)
            await _notify(
                title="Pebble: failed",
                body=f"{type(exc).__name__}: {str(exc)[:240]}",
                success=False,
            )
        finally:
            duration_ms = int((time.monotonic() - t0) * 1000)
            span.set_attribute("pebble.duration_ms", duration_ms)
            span.set_attribute("pebble.outcome", outcome)
            span.set_attribute("pebble.success", outcome == "success")


async def worker_loop(queue: JobQueue, *, timeout_s: int, kb_path: str) -> None:
    """Pop jobs from the queue and process them serially. Runs until cancelled."""
    log.info(
        "Pebble worker loop started (timeout_s=%d, kb_path=%s)",
        timeout_s, kb_path,
    )
    while True:
        job = await queue.get()
        try:
            await process_job(job, timeout_s=timeout_s, kb_path=kb_path)
        except Exception:
            log.exception("[%s] unhandled error in process_job", job.id)
