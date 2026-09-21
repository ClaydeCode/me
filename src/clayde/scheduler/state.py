"""Container-owned scheduler run-state (recurring dedup)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("recurring", {})
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def get_last_fired(state: dict, key: str) -> datetime | None:
    entry = state.get("recurring", {}).get(key)
    if not entry or "last_fired_at" not in entry:
        return None
    return datetime.fromisoformat(entry["last_fired_at"])


def set_last_fired(state: dict, key: str, dt: datetime) -> None:
    state.setdefault("recurring", {})[key] = {"last_fired_at": dt.isoformat()}
