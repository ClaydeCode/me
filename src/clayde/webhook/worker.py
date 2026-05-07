"""Background worker: pop jobs and invoke the Claude CLI."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time

from clayde.telemetry import get_tracer
from clayde.webhook.queue import JobQueue, PebbleJob
from clayde.webhook.runner import invoke_claude_pebble
from clayde.webhook.skills import (
    SKILLS_ROOT,
    build_system_prompt,
    build_user_prompt,
    discover_skills,
)

log = logging.getLogger("clayde.webhook.worker")


async def process_job(job: PebbleJob, *, timeout_s: int) -> None:
    """Process a single Pebble job. Records an OTel ``clayde.pebble.process`` span."""
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
        with tempfile.TemporaryDirectory(prefix=f"clayde-pebble-{job.id}-") as cwd:
            try:
                output = await invoke_claude_pebble(
                    system_prompt=system_prompt,
                    user_text=user_text,
                    cwd=cwd,
                    timeout_s=timeout_s,
                )
                if output.strip() == "No matching skill":
                    span.set_attribute("pebble.skill", "none")
                span.set_attribute("pebble.success", True)
                log.info("[%s] processed (output: %d chars)", job.id, len(output))
            except Exception as e:
                span.set_attribute("pebble.success", False)
                span.set_attribute("error.type", type(e).__name__)
                span.set_attribute("error.message", str(e))
                span.record_exception(e)
                log.exception("[%s] failed: %s", job.id, e)
                raise
            finally:
                duration_ms = int((time.monotonic() - t0) * 1000)
                span.set_attribute("pebble.duration_ms", duration_ms)


async def worker_loop(queue: JobQueue, *, timeout_s: int) -> None:
    """Pop jobs from the queue and process them serially. Runs until cancelled."""
    log.info("Pebble worker loop started (timeout_s=%d)", timeout_s)
    while True:
        job = await queue.get()
        try:
            await process_job(job, timeout_s=timeout_s)
        except Exception:
            # Already logged in process_job; keep the loop alive.
            pass
