"""Issue state persistence (state.json)."""

import json
import logging

from clayde.config import DATA_DIR

log = logging.getLogger("clayde.state")

_STATE_FILE = DATA_DIR / "state.json"


def load_state() -> dict:
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text())
    return {"issues": {}}


def save_state(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def get_issue_state(issue_url: str) -> dict:
    return load_state()["issues"].get(issue_url, {})


def update_issue_state(issue_url: str, updates: dict) -> None:
    state = load_state()
    entry = state["issues"].setdefault(issue_url, {})
    entry.update(updates)
    save_state(state)
