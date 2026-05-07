"""Skill discovery and system-prompt construction for the Pebble webhook."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger("clayde.webhook")

SKILLS_ROOT = Path("/skills")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path


def _parse_skill(path: Path) -> Skill:
    """Parse a skill markdown file. Raises ValueError on malformed input."""
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"missing frontmatter in {path}")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError(f"unterminated frontmatter in {path}")
    fm_text = text[4:end]
    data = yaml.safe_load(fm_text) or {}
    name = data.get("name")
    desc = data.get("description")
    if isinstance(name, str):
        name = name.strip()
    if isinstance(desc, str):
        desc = desc.strip()
    if not isinstance(name, str) or not isinstance(desc, str) or not name or not desc:
        raise ValueError(f"name and description required in frontmatter of {path}")
    return Skill(name=name, description=desc, path=path)
