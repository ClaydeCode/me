from datetime import datetime
from pathlib import Path
import pytest
from clayde.scheduler.tasks import parse_task_file, discover_tasks, ScheduledTask

def _w(p: Path, fm: str, body: str = "do the thing"):
    p.write_text(f"---\n{fm}\n---\n{body}\n")

def test_parse_cron(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"')
    t = parse_task_file(f, "Europe/Berlin")
    assert t.cron == "0 8 * * *" and t.at is None and t.enabled is True
    assert t.prompt.strip() == "do the thing"

def test_parse_at(tmp_path):
    f = tmp_path / "k.md"; _w(f, "at: 2026-09-21T08:00")
    t = parse_task_file(f, "Europe/Berlin")
    assert t.at == datetime(2026, 9, 21, 8, 0, tzinfo=t.tz) and t.cron is None

def test_both_keys_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\nat: 2026-09-21T08:00')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin")

def test_neither_key_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, "title: x")
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin")

def test_bad_cron_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "not a cron"')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin")

def test_bad_tz_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntz: Mars/Phobos')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin")

def test_enabled_false(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\nenabled: false')
    assert parse_task_file(f, "Europe/Berlin").enabled is False

def test_discover_skips_done_and_malformed(tmp_path, caplog):
    _w(tmp_path / "good.md", 'cron: "0 8 * * *"')
    (tmp_path / "bad.md").write_text("no frontmatter")
    (tmp_path / "done").mkdir()
    _w(tmp_path / "done" / "old.md", 'cron: "0 8 * * *"')
    tasks = discover_tasks(tmp_path, "Europe/Berlin")
    assert [t.path.name for t in tasks] == ["good.md"]
