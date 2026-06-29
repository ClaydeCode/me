"""Always-on entry point for the Freeshard execution loop.

Mirrors clayde.orchestrator.run_loop but drives the stateless Freeshard
tick instead of the main Clayde orchestrator cycle.

Entry point:
  run_loop() — signal-handled loop calling tick() then sleeping.
               Pointed at by pyproject.toml [project.scripts] in Task 11.
"""

import logging
import os
import signal
import subprocess
import sys
import time

from clayde.claude import is_claude_available
from clayde.config import get_github_client, get_settings, setup_logging
from clayde.disk import check_disk_and_alert
from clayde.freeshard.loop import tick

log = logging.getLogger("clayde.freeshard.entry")

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("Received signal %s — will shut down after current tick", signum)


def run_loop() -> None:
    """Run tick() in a loop with a configurable sleep interval.

    This is the Freeshard container entry point.
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    setup_logging()
    settings = get_settings()
    os.environ["GH_TOKEN"] = settings.github_token

    git_name = settings.effective_git_name
    git_email = settings.git_email
    if not git_name or not git_email:
        log.error(
            "CLAYDE_GIT_NAME (or CLAYDE_GITHUB_USERNAME) and CLAYDE_GIT_EMAIL"
            " must be set to non-empty strings"
        )
        sys.exit(1)
    subprocess.run(["git", "config", "--global", "user.name", git_name], check=True)
    subprocess.run(["git", "config", "--global", "user.email", git_email], check=True)

    log.info("Starting Freeshard loop (interval=%ds)", settings.fs_loop_interval_s)

    while not _shutdown:
        try:
            check_disk_and_alert(settings)
        except Exception:
            log.warning("Disk guard check failed — continuing")
        try:
            if is_claude_available():
                g = get_github_client()
                n = tick(g, settings)
                log.info("Freeshard tick complete — processed %d issues", n)
            else:
                log.info("usage limit — skipping")
        except Exception:
            log.exception("Unhandled error in Freeshard tick")

        for _ in range(settings.fs_loop_interval_s):
            if _shutdown:
                break
            time.sleep(1)
