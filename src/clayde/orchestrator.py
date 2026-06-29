"""Clayde orchestrator — Pebble voice webhook host."""

import asyncio
import logging
import signal

import uvicorn

from clayde.config import get_settings, setup_logging
from clayde.webhook import JobQueue, create_app, worker_loop

log = logging.getLogger("clayde.orchestrator")

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("Received signal %s — will shut down", signum)


async def _run_with_pebble() -> None:
    """Async entry point that runs the Pebble webhook and worker."""
    setup_logging()
    settings = get_settings()
    log.info(
        "Starting Clayde with Pebble webhook (port=%d, queue_max=%d)",
        settings.pebble_port, settings.pebble_queue_max,
    )

    queue = JobQueue(maxsize=settings.pebble_queue_max)
    app = create_app(queue=queue, expected_token=settings.pebble_token)
    config = uvicorn.Config(
        app, host="0.0.0.0", port=settings.pebble_port,
        log_level="info", access_log=True, lifespan="off",
    )
    server = uvicorn.Server(config)

    async def worker_task() -> None:
        await worker_loop(
            queue,
            timeout_s=settings.pebble_timeout,
            kb_path=settings.kb_path,
        )

    await asyncio.gather(server.serve(), worker_task())


def run_loop():
    """Container entry point — runs the Pebble webhook host."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(_run_with_pebble())
