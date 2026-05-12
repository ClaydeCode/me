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


_SYSTEM_PROMPT_TEMPLATE = """\
You are Clayde, acting on a voice command from the user via a Pebble watch.

The text you receive is speech-to-text output. It MAY contain transcription
errors. Consider phonetically similar words and the most likely intent —
e.g. "calendar" might arrive as "colander". Use judgement.

{skill_section}

Choose AT MOST ONE skill per command. If no skill matches, respond with
exactly "No matching skill" and stop. Do not invent or improvise. Do not
chain multiple skills.
"""


def build_system_prompt(skills: list[Skill]) -> str:
    """Build the system prompt sent to the Claude CLI for a Pebble request."""
    if not skills:
        skill_section = "Available skills: (no skills available)"
    else:
        catalog = "\n".join(f"- {s.name}: {s.description}" for s in skills)
        files = "\n".join(f"- {s.name}: {s.path}" for s in skills)
        skill_section = (
            "Available skills:\n\n"
            f"{catalog}\n\n"
            "To use a skill, read the full file at the path noted, then follow it.\n"
            "Skill files:\n\n"
            f"{files}"
        )
    return _SYSTEM_PROMPT_TEMPLATE.format(skill_section=skill_section)


def build_user_prompt(text: str, timestamp: int) -> str:
    """Build the user prompt (passed to ``claude -p``) for a Pebble request."""
    return f"(timestamp {timestamp})\n{text}"


def discover_skills(root: Path = SKILLS_ROOT) -> list[Skill]:
    """Recursively discover all skills under ``root``.

    Returns a list ordered alphabetically by full path. On duplicate
    ``name`` fields, the first-discovered skill wins; subsequent
    duplicates are logged at WARNING and ignored. Malformed files are
    logged at WARNING and skipped.
    """
    if not root.exists():
        return []
    files = sorted(root.rglob("*.md"))
    seen: dict[str, Skill] = {}
    for path in files:
        try:
            skill = _parse_skill(path)
        except (ValueError, OSError) as e:
            log.warning("Failed to parse skill %s: %s", path, e)
            continue
        if skill.name in seen:
            log.warning(
                "Duplicate skill name %r — keeping %s, ignoring %s",
                skill.name, seen[skill.name].path, path,
            )
            continue
        seen[skill.name] = skill
    return list(seen.values())
