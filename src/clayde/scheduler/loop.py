"""Scheduler tick loop and its pure scheduling helpers."""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from croniter import croniter

from clayde.scheduler.state import (
    load_state, save_state, get_last_fired, set_last_fired,
)
from clayde.scheduler.tasks import discover_tasks
from clayde.service.queue import Job, JobQueue, QueueFullError

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


def _move_to_done(path: Path, now: datetime) -> None:
    done = path.parent / "done"
    done.mkdir(exist_ok=True)
    shutil.move(str(path), str(done / f"{int(now.timestamp())}-{path.name}"))


def run_tick(queue: JobQueue, *, tasks_dir: Path, state_path: Path,
             default_tz: str, now: datetime) -> None:
    state = load_state(state_path)
    for task in discover_tasks(tasks_dir, default_tz):
        if not task.enabled:
            continue
        key = task.path.name
        scheduled: datetime | None = None

        if task.cron is not None:
            last = get_last_fired(state, key)
            if last is None:
                set_last_fired(state, key, baseline_recurring(task.cron, task.tz, now))
                continue
            scheduled = recurring_due(task.cron, task.tz, now, last)
        elif oneoff_due(task.at, now):
            scheduled = task.at

        if scheduled is None:
            continue

        note = lateness_note(scheduled, now)
        text = f"{note}\n{task.prompt}" if note else task.prompt
        job = Job(id=str(uuid.uuid4()), text=text,
                  timestamp=int(now.timestamp()), origin="scheduler")
        try:
            queue.enqueue(job)
        except QueueFullError:
            log.warning("Queue full — deferring task %s", key)
            continue
        if task.cron is not None:
            set_last_fired(state, key, scheduled)
        else:
            _move_to_done(task.path, now)

    save_state(state_path, state)


async def scheduler_loop(queue: JobQueue, *, tasks_dir: str, state_path: str,
                          default_tz: str, interval_s: int) -> None:
    log.info("Scheduler loop started (dir=%s, interval=%ds)", tasks_dir, interval_s)
    while True:
        try:
            run_tick(queue, tasks_dir=Path(tasks_dir), state_path=Path(state_path),
                     default_tz=default_tz, now=datetime.now(timezone.utc))
        except Exception:
            log.exception("Scheduler tick failed — continuing")
        await asyncio.sleep(interval_s)
