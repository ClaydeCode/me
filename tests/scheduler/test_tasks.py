import logging
from datetime import datetime
from pathlib import Path
import pytest
from clayde.scheduler.tasks import (
    parse_task_file, discover_tasks, ScheduledTask, MAX_TIMEOUT_S,
)

DEFAULT_TIMEOUT_S = 300

def _w(p: Path, fm: str, body: str = "do the thing"):
    p.write_text(f"---\n{fm}\n---\n{body}\n")

def test_parse_cron(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"')
    t = parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert t.cron == "0 8 * * *" and t.at is None and t.enabled is True
    assert t.prompt.strip() == "do the thing"

def test_parse_at(tmp_path):
    f = tmp_path / "k.md"; _w(f, "at: 2026-09-21T08:00")
    t = parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert t.at == datetime(2026, 9, 21, 8, 0, tzinfo=t.tz) and t.cron is None

def test_both_keys_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\nat: 2026-09-21T08:00')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)

def test_neither_key_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, "title: x")
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)

def test_bad_cron_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "not a cron"')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)

def test_bad_tz_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntz: Mars/Phobos')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)

def test_enabled_false(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\nenabled: false')
    assert parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S).enabled is False

def test_discover_skips_done_and_malformed(tmp_path, caplog):
    _w(tmp_path / "good.md", 'cron: "0 8 * * *"')
    (tmp_path / "bad.md").write_text("no frontmatter")
    (tmp_path / "done").mkdir()
    _w(tmp_path / "done" / "old.md", 'cron: "0 8 * * *"')
    tasks = discover_tasks(tmp_path, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert [t.path.name for t in tasks] == ["good.md"]

def test_timeout_absent_uses_default(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"')
    t = parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert t.timeout_s == DEFAULT_TIMEOUT_S

def test_timeout_hours(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntimeout: 4h')
    t = parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert t.timeout_s == 14400

def test_timeout_minutes(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntimeout: 90m')
    t = parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert t.timeout_s == 5400

def test_timeout_seconds_suffix(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntimeout: 45s')
    t = parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert t.timeout_s == 45

def test_timeout_bare_seconds(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntimeout: 600')
    t = parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert t.timeout_s == 600

def test_timeout_over_cap_clamped_and_warns(tmp_path, caplog):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntimeout: 9h')
    with caplog.at_level(logging.WARNING):
        t = parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert t.timeout_s == MAX_TIMEOUT_S == 14400
    assert any("timeout" in r.message.lower() for r in caplog.records)

def test_timeout_malformed_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntimeout: soon')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin", DEFAULT_TIMEOUT_S)

def test_timeout_malformed_skipped_by_discover(tmp_path):
    _w(tmp_path / "bad.md", 'cron: "0 8 * * *"\ntimeout: soon')
    tasks = discover_tasks(tmp_path, "Europe/Berlin", DEFAULT_TIMEOUT_S)
    assert tasks == []
