"""Issue state persistence (state.json)."""

import json
import logging

from opentelemetry import trace

from clayde.config import DATA_DIR

log = logging.getLogger("clayde.state")

_STATE_FILE = DATA_DIR / "state.json"


def load_state():
    if _STATE_FILE.exists():
        return json.loads(_STATE_FILE.read_text())
    return {"issues": {}}


def save_state(state):
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def get_issue_state(issue_url):
    return load_state()["issues"].get(issue_url, {})


def update_issue_state(issue_url, updates):
    state = load_state()
    entry = state["issues"].setdefault(issue_url, {})
    old_status = entry.get("status")
    entry.update(updates)
    new_status = entry.get("status")
    save_state(state)

    if old_status != new_status:
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event("state_transition", attributes={
                "issue.url": issue_url,
                "old_status": old_status or "(none)",
                "new_status": new_status or "(none)",
            })


def accumulate_cost(issue_url: str, cost_eur: float) -> None:
    """Add cost to the running total for this issue."""
    state = load_state()
    entry = state["issues"].setdefault(issue_url, {})
    entry["accumulated_cost_eur"] = entry.get("accumulated_cost_eur", 0.0) + cost_eur
    save_state(state)


def pop_accumulated_cost(issue_url: str) -> float:
    """Return and reset the accumulated cost for this issue."""
    state = load_state()
    entry = state["issues"].get(issue_url, {})
    cost = entry.pop("accumulated_cost_eur", 0.0)
    save_state(state)
    return cost
