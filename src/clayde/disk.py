"""Best-effort host disk-usage guard.

Clayde runs in a container whose ``/data`` bind-mount lives on the host root
partition, so ``shutil.disk_usage("/data")`` reflects how full the host disk
is. When usage crosses a threshold we ntfy Max so the disk gets cleaned before
it fills up (a full disk silently breaks clones, builds, and the agent loop).

Best-effort: any error is logged, never raised — a missed alert must never
take down the main loop. Re-alerts are rate-limited by a cooldown persisted in
a tiny JSON file so the 5-minute tick loop doesn't spam the same warning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

from clayde.config import DATA_DIR, Settings
from clayde.webhook.notify import send_ntfy

log = logging.getLogger("clayde.disk")


def _default_state_path() -> Path:
    return DATA_DIR / "disk_alert_state.json"


def _read_last_alert_ts(path: Path) -> float:
    try:
        return float(json.loads(path.read_text()).get("last_alert_ts", 0.0))
    except (OSError, ValueError, TypeError):
        return 0.0


def _write_last_alert_ts(path: Path, ts: float) -> None:
    try:
        path.write_text(json.dumps({"last_alert_ts": ts}))
    except OSError as exc:
        log.warning("could not persist disk alert state: %s", exc)


def _send(settings: Settings, *, usage_pct: int, free_gb: float) -> None:
    """Emit a disk-full warning via the shared ntfy helper.

    Reuses ``webhook.notify.send_ntfy`` (itself best-effort — errors logged,
    never raised). ``success=False`` gives it warning styling (priority 5,
    rotating_light tag). Called from the sync tick loop, so the async helper
    is driven via ``asyncio.run`` — safe here because ``main()`` never runs
    inside an active event loop (in Pebble mode it runs via ``to_thread``).
    """
    title = f"clayde.net disk {usage_pct}% full"
    body = (
        f"Only {free_gb:.1f} GB free on {settings.disk_alert_path}. "
        "Run disk cleanup (KB: vm-disk-cleanup)."
    )
    asyncio.run(
        send_ntfy(
            title=title,
            body=body,
            success=False,
            base_url=settings.ntfy_base_url,
            topic=settings.ntfy_topic,
            timeout_s=settings.ntfy_timeout_s,
        )
    )


def check_disk_and_alert(
    settings: Settings,
    *,
    state_path: Path | None = None,
    now: float | None = None,
) -> int | None:
    """Check disk usage and ntfy when at/over the threshold (rate-limited).

    Returns the usage percentage, or ``None`` when disabled or the check
    itself failed. Never raises.
    """
    if not settings.disk_alert_enabled:
        return None
    try:
        total, used, free = shutil.disk_usage(settings.disk_alert_path)
    except OSError as exc:
        log.warning(
            "disk usage check failed for %s: %s", settings.disk_alert_path, exc
        )
        return None

    usage_pct = round(used / total * 100)
    if usage_pct < settings.disk_alert_threshold_pct:
        return usage_pct

    now = time.time() if now is None else now
    sf = state_path if state_path is not None else _default_state_path()
    last = _read_last_alert_ts(sf)
    if last and now - last < settings.disk_alert_cooldown_s:
        log.info("disk %d%% over threshold but within alert cooldown", usage_pct)
        return usage_pct

    log.warning(
        "disk %d%% >= threshold %d%% — sending alert",
        usage_pct,
        settings.disk_alert_threshold_pct,
    )
    _send(settings, usage_pct=usage_pct, free_gb=free / 1e9)
    _write_last_alert_ts(sf, now)
    return usage_pct
