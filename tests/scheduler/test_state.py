from datetime import datetime, timezone
from clayde.scheduler.state import load_state, save_state, get_last_fired, set_last_fired

def test_missing_file_is_empty(tmp_path):
    assert load_state(tmp_path / "none.json") == {"recurring": {}}

def test_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    state = load_state(p)
    dt = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    set_last_fired(state, "keep-warm.md", dt)
    save_state(p, state)
    again = load_state(p)
    assert get_last_fired(again, "keep-warm.md") == dt

def test_get_missing_key_is_none(tmp_path):
    assert get_last_fired(load_state(tmp_path / "s.json"), "x") is None
