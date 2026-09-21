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
You are Clayde, executing a request from the user via a Pebble watch.

You have a hard wall-clock budget of {timeout_s} seconds for this
entire request. If your process exceeds it, it is killed and the user gets
no result. Scope your work to fit: prefer a smaller, complete action over an
ambitious one that risks timing out. If the request is too big to finish in
time, do the most valuable part you can and say so in the JSON summary.

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


def build_system_prompt(skills: list[Skill], timeout_s: int = 300) -> str:
    """Build the system prompt sent to the Claude CLI for a Pebble request.

    ``timeout_s`` is the hard wall-clock budget enforced by the runner; it is
    surfaced in the prompt so Claude can scope work to fit.
    """
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
    return _SYSTEM_PROMPT_TEMPLATE.format(
        skill_section=skill_section, timeout_s=timeout_s,
    )


def build_user_prompt(text: str, timestamp: int) -> str:
    """Build the user prompt (passed to ``claude -p``) for a Pebble request."""
    return f"(timestamp {timestamp})\n{text}"


def _is_builtin(path: Path) -> bool:
    """Return True if *path* lives under the ``builtin/`` subdirectory."""
    return "builtin" in {p.name for p in path.parents}


def discover_skills(root: Path = SKILLS_ROOT) -> list[Skill]:
    """Recursively discover all skills under ``root``.

    Returns a list ordered alphabetically by full path. Non-builtin skills
    (those NOT under a ``builtin/`` subdirectory) are processed before
    builtin skills so that user-mounted overrides take priority over
    shipped defaults. On duplicate ``name`` fields after ordering, the
    first-encountered skill wins; subsequent duplicates are logged at
    WARNING and ignored. Malformed files are logged at WARNING and skipped.
    """
    if not root.exists():
        return []
    all_files = sorted(root.rglob("*.md"))
    # Non-builtin first so user skills override shipped builtins on name collision.
    files = [f for f in all_files if not _is_builtin(f)]
    files += [f for f in all_files if _is_builtin(f)]
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
