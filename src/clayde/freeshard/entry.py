"""Always-on entry point for the Freeshard execution loop.

Mirrors clayde.orchestrator.run_loop but drives the stateless Freeshard
tick instead of the main Clayde orchestrator cycle.

Entry point:
  run_loop() — signal-handled loop calling run_cycle() then sleeping.
               Pointed at by pyproject.toml [project.scripts] in Task 11.
"""

import logging
import signal
import time

from clayde.config import get_settings, setup_logging
from clayde.freeshard.loop import run_cycle

log = logging.getLogger("clayde.freeshard.entry")

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("Received signal %s — will shut down after current cycle", signum)


def run_loop() -> None:
    """Run run_cycle() in a loop with a configurable sleep interval.

    This is the Freeshard container entry point (standalone dev/manual runs).
    In production the loop runs inside the Pebble process via orchestrator.py.
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    setup_logging()
    settings = get_settings()

    log.info("Starting Freeshard loop (interval=%ds)", settings.fs_loop_interval_s)

    while not _shutdown:
        try:
            n = run_cycle(settings)
            if n >= 0:
                log.info("Freeshard tick complete — processed %d issues", n)
        except Exception:
            log.exception("Unhandled error in Freeshard cycle")

        for _ in range(settings.fs_loop_interval_s):
            if _shutdown:
                break
            time.sleep(1)
