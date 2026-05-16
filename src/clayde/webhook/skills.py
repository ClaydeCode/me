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
You are Clayde, executing a voice command from the user via a Pebble watch.

The text you receive is speech-to-text output. It MAY contain transcription
errors. Consider phonetically similar words and the most likely intent —
e.g. "calendar" might arrive as "colander". Use judgement.

Default working target: /home/clayde/knowledge_base (mounted RW, synced
via Syncthing). If the command implies "remember this", "note", "save",
"log", or "capture", write a file there. No git operations — Syncthing
handles sync.

{skill_section}

Skills are suggestions, not constraints. Use as many as the command needs,
in any order. If no skill fits, use your judgement — capture into the
knowledge base inbox or answer directly.

When done, your LAST output MUST be a single fenced JSON block in this
exact form:

```json
{{"title": "<short, max 40 chars>", "body": "<message, max 300 chars>", "success": true}}
```

Set `success` to false only if you could not carry out the user's intent.
Anything before the JSON block is your working narrative and is ignored
by the framework.
"""


def build_system_prompt(skills: list[Skill]) -> str:
    """Build the system prompt sent to the Claude CLI for a Pebble request."""
    if not skills:
        skill_section = "Available skills: (none currently registered)"
    else:
        catalog = "\n".join(f"- {s.name}: {s.description}" for s in skills)
        files = "\n".join(f"- {s.name}: {s.path}" for s in skills)
        skill_section = (
            "Available skills (read the full file before using):\n\n"
            f"{catalog}\n\n"
            "Skill file paths:\n\n"
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
        except Exception as e:
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
