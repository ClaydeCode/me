"""Scheduler tick loop and its pure scheduling helpers."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from croniter import croniter

log = logging.getLogger("clayde.scheduler")

_LATE_THRESHOLD = timedelta(seconds=60)


def baseline_recurring(cron: str, tz, now: datetime) -> datetime:
    start = now.astimezone(tz) + timedelta(seconds=1)
    return croniter(cron, start).get_prev(datetime)


def recurring_due(cron: str, tz, now: datetime, last_fired: datetime) -> datetime | None:
    start = now.astimezone(tz) + timedelta(seconds=1)
    prev = croniter(cron, start).get_prev(datetime)
    return prev if prev > last_fired else None


def oneoff_due(at: datetime, now: datetime) -> bool:
    return now >= at


def lateness_note(scheduled: datetime, now: datetime) -> str | None:
    delta = now - scheduled
    if delta <= _LATE_THRESHOLD:
        return None
    mins = int(delta.total_seconds() // 60)
    when = scheduled.strftime("%Y-%m-%d %H:%M %Z")
    dur = f"{mins} min" if mins else f"{int(delta.total_seconds())} s"
    return f"[This task was scheduled for {when} and is running {dur} late.]"
