from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from clayde.service.queue import JobQueue
from clayde.scheduler.loop import run_tick

TZ = "Europe/Berlin"

def _w(d: Path, name: str, fm: str, body="do it"):
    (d / name).write_text(f"---\n{fm}\n---\n{body}\n")

async def _drain(q: JobQueue):
    out = []
    while not q._q.empty():
        out.append(await q.get())
    return out

async def test_first_encounter_baselines_without_firing(tmp_path):
    _w(tmp_path, "k.md", 'cron: "0 8 * * *"')
    q = JobQueue(maxsize=10)
    now = datetime(2026, 9, 21, 15, 0, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=tmp_path / "s.json", default_tz=TZ, now=now, default_timeout_s=300)
    assert await _drain(q) == []
    assert (tmp_path / "s.json").exists()

async def test_recurring_fires_and_dedups(tmp_path):
    _w(tmp_path, "k.md", 'cron: "0 8 * * *"')
    q = JobQueue(maxsize=10)
    sp = tmp_path / "s.json"
    # seed state so it's not first-encounter
    from clayde.scheduler.state import load_state, save_state, set_last_fired
    st = load_state(sp); set_last_fired(st, "k.md", datetime(2026, 9, 20, 8, 0, tzinfo=ZoneInfo(TZ))); save_state(sp, st)
    now = datetime(2026, 9, 21, 8, 0, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=sp, default_tz=TZ, now=now, default_timeout_s=300)
    jobs = await _drain(q)
    assert len(jobs) == 1 and jobs[0].origin == "scheduler"
    # second tick same minute: no duplicate
    run_tick(q, tasks_dir=tmp_path, state_path=sp, default_tz=TZ, now=now, default_timeout_s=300)
    assert await _drain(q) == []

async def test_oneoff_fires_and_moves_to_done(tmp_path):
    _w(tmp_path, "call.md", "at: 2026-09-21T08:00")
    q = JobQueue(maxsize=10)
    now = datetime(2026, 9, 21, 8, 1, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=tmp_path / "s.json", default_tz=TZ, now=now, default_timeout_s=300)
    jobs = await _drain(q)
    assert len(jobs) == 1
    assert not (tmp_path / "call.md").exists()
    assert list((tmp_path / "done").glob("*call.md"))

async def test_late_oneoff_prepends_note(tmp_path):
    _w(tmp_path, "call.md", "at: 2026-09-21T08:00", body="ring the bell")
    q = JobQueue(maxsize=10)
    now = datetime(2026, 9, 21, 9, 0, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=tmp_path / "s.json", default_tz=TZ, now=now, default_timeout_s=300)
    jobs = await _drain(q)
    assert "late" in jobs[0].text.lower() and "ring the bell" in jobs[0].text

async def test_job_gets_task_timeout(tmp_path):
    _w(tmp_path, "call.md", 'at: 2026-09-21T08:00\ntimeout: 2h')
    q = JobQueue(maxsize=10)
    now = datetime(2026, 9, 21, 8, 1, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=tmp_path / "s.json", default_tz=TZ, now=now, default_timeout_s=300)
    jobs = await _drain(q)
    assert jobs[0].timeout_s == 7200

async def test_job_gets_default_timeout_when_absent(tmp_path):
    _w(tmp_path, "call.md", "at: 2026-09-21T08:00")
    q = JobQueue(maxsize=10)
    now = datetime(2026, 9, 21, 8, 1, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=tmp_path / "s.json", default_tz=TZ, now=now, default_timeout_s=300)
    jobs = await _drain(q)
    assert jobs[0].timeout_s == 300

async def test_failing_task_does_not_lose_earlier_dedup(tmp_path, monkeypatch):
    # "a-cron.md" sorts before "b-oneoff.md" so the recurring task fires
    # first in the same tick as the one-off task that then blows up.
    _w(tmp_path, "a-cron.md", 'cron: "0 8 * * *"')
    _w(tmp_path, "b-oneoff.md", "at: 2026-09-21T08:00")
    q = JobQueue(maxsize=10)
    sp = tmp_path / "s.json"
    from clayde.scheduler.state import load_state, save_state, set_last_fired, get_last_fired
    st = load_state(sp)
    set_last_fired(st, "a-cron.md", datetime(2026, 9, 20, 8, 0, tzinfo=ZoneInfo(TZ)))
    save_state(sp, st)
    now = datetime(2026, 9, 21, 8, 0, tzinfo=ZoneInfo(TZ))

    import clayde.scheduler.loop as loop_mod
    def _boom(path, now):
        raise OSError("permission denied")
    monkeypatch.setattr(loop_mod, "_move_to_done", _boom)

    run_tick(q, tasks_dir=tmp_path, state_path=sp, default_tz=TZ, now=now, default_timeout_s=300)

    jobs = await _drain(q)
    # both jobs got enqueued before the one-off's move-to-done blew up
    assert len(jobs) == 2

    reloaded = load_state(sp)
    fired_at = get_last_fired(reloaded, "a-cron.md")
    assert fired_at is not None and fired_at == datetime(2026, 9, 21, 8, 0, tzinfo=ZoneInfo(TZ))

    # a second tick at the same `now`: the recurring task must not re-fire
    # (its dedup was persisted); the still-broken one-off re-enqueues, which
    # is the documented (not this fix's) consequence of the move continuing
    # to fail.
    run_tick(q, tasks_dir=tmp_path, state_path=sp, default_tz=TZ, now=now, default_timeout_s=300)
    second_jobs = await _drain(q)
    assert len(second_jobs) == 1
    assert "do it" in second_jobs[0].text
