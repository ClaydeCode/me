from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from clayde.scheduler.loop import (
    baseline_recurring, recurring_due, oneoff_due, lateness_note,
)

TZ = ZoneInfo("Europe/Berlin")

def _at(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=TZ)

def test_baseline_is_last_past_occurrence(tmp=None):
    now = _at(2026, 9, 21, 15, 0)
    assert baseline_recurring("0 8 * * *", TZ, now) == _at(2026, 9, 21, 8, 0)

def test_recurring_fires_once_after_occurrence():
    now = _at(2026, 9, 21, 8, 0)
    last = _at(2026, 9, 20, 8, 0)
    assert recurring_due("0 8 * * *", TZ, now, last) == _at(2026, 9, 21, 8, 0)

def test_recurring_not_due_when_already_fired():
    now = _at(2026, 9, 21, 8, 30)
    last = _at(2026, 9, 21, 8, 0)
    assert recurring_due("0 8 * * *", TZ, now, last) is None

def test_recurring_single_fire_after_downtime():
    # down for two days; only the most recent occurrence fires, once
    now = _at(2026, 9, 23, 9, 0)
    last = _at(2026, 9, 20, 8, 0)
    assert recurring_due("0 8 * * *", TZ, now, last) == _at(2026, 9, 23, 8, 0)

def test_oneoff_due():
    assert oneoff_due(_at(2026, 9, 21, 8, 0), _at(2026, 9, 21, 8, 1)) is True
    assert oneoff_due(_at(2026, 9, 21, 8, 0), _at(2026, 9, 21, 7, 59)) is False

def test_lateness_note_present_when_late():
    note = lateness_note(_at(2026, 9, 21, 8, 0), _at(2026, 9, 21, 9, 0))
    assert note is not None and "late" in note.lower()

def test_lateness_note_absent_when_on_time():
    assert lateness_note(_at(2026, 9, 21, 8, 0), _at(2026, 9, 21, 8, 0, 30)) is None
