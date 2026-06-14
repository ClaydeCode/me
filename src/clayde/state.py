"""Issue state persistence (state.json)."""

import json
import logging

from clayde.config import DATA_DIR

log = logging.getLogger("clayde.state")

_STATE_FILE = DATA_DIR / "state.json"

# Top-level (non-issue) state key tracking whether the operator has been
# alerted about the current Claude auth-failure streak, so the alert fires
# once per streak rather than every cycle.
_CLAUDE_AUTH_NOTIFIED_KEY = "claude_auth_failure_notified"


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


def get_claude_auth_notified() -> bool:
    """Return whether an alert has been sent for the current auth-failure streak."""
    return bool(load_state().get(_CLAUDE_AUTH_NOTIFIED_KEY, False))


def set_claude_auth_notified(value: bool) -> None:
    """Record whether the current Claude auth-failure streak has been notified."""
    state = load_state()
    state[_CLAUDE_AUTH_NOTIFIED_KEY] = value
    save_state(state)
