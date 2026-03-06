"""Issue state persistence (state.json)."""

import json
import logging
import os

from opentelemetry import trace

from clayde.config import get_settings

log = logging.getLogger("clayde.state")


def load_state():
    state_file = get_settings().state_file
    if os.path.exists(state_file):
        with open(state_file) as f:
            return json.load(f)
    return {"issues": {}}


def save_state(state):
    with open(get_settings().state_file, "w") as f:
        json.dump(state, f, indent=2)


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
