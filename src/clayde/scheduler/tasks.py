"""Scheduled-task markdown files: model, parsing, discovery."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from croniter import croniter

log = logging.getLogger("clayde.scheduler")


@dataclass(frozen=True)
class ScheduledTask:
    path: Path
    prompt: str
    cron: str | None
    at: datetime | None
    tz: ZoneInfo
    enabled: bool
    title: str | None


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("unterminated frontmatter")
    data = yaml.safe_load(text[4:end]) or {}
    body = text[end + 4:].lstrip("\n")
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    return data, body


def parse_task_file(path: Path, default_tz: str) -> ScheduledTask:
    data, body = _split_frontmatter(path.read_text())

    cron = data.get("cron")
    at_raw = data.get("at")
    if (cron is None) == (at_raw is None):
        raise ValueError("exactly one of 'cron' or 'at' is required")

    tz_name = data.get("tz", default_tz)
    try:
        tz = ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"bad tz {tz_name!r}") from e

    if cron is not None:
        cron = str(cron)
        if not croniter.is_valid(cron):
            raise ValueError(f"bad cron {cron!r}")
        at = None
    else:
        cron = None
        base = at_raw if isinstance(at_raw, datetime) else datetime.fromisoformat(str(at_raw))
        at = base.replace(tzinfo=tz) if base.tzinfo is None else base

    enabled = bool(data.get("enabled", True))
    title = data.get("title")
    return ScheduledTask(
        path=path, prompt=body, cron=cron, at=at, tz=tz,
        enabled=enabled, title=str(title) if title is not None else None,
    )


def discover_tasks(root: Path, default_tz: str) -> list[ScheduledTask]:
    if not root.exists():
        return []
    tasks: list[ScheduledTask] = []
    for p in sorted(root.glob("*.md")):
        try:
            tasks.append(parse_task_file(p, default_tz))
        except Exception as e:
            log.warning("Skipping malformed task file %s: %s", p, e)
    return tasks
