from pathlib import Path

import pytest

from clayde.webhook.skills import Skill, _parse_skill


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_parse_skill_minimal(tmp_path):
    p = _write(tmp_path / "note.md", """\
---
name: add-note
description: Append a note to the knowledge repo.
---

Body here.
""")
    skill = _parse_skill(p)
    assert skill == Skill(name="add-note", description="Append a note to the knowledge repo.", path=p)


def test_parse_skill_missing_frontmatter(tmp_path):
    p = _write(tmp_path / "broken.md", "no frontmatter here\n")
    with pytest.raises(ValueError, match="missing frontmatter"):
        _parse_skill(p)


def test_parse_skill_unterminated_frontmatter(tmp_path):
    p = _write(tmp_path / "broken.md", "---\nname: foo\ndescription: bar\n")
    with pytest.raises(ValueError, match="unterminated frontmatter"):
        _parse_skill(p)


def test_parse_skill_missing_name(tmp_path):
    p = _write(tmp_path / "broken.md", "---\ndescription: only a description\n---\n\nBody.\n")
    with pytest.raises(ValueError, match="name and description required"):
        _parse_skill(p)


def test_parse_skill_missing_description(tmp_path):
    p = _write(tmp_path / "broken.md", "---\nname: only-a-name\n---\n\nBody.\n")
    with pytest.raises(ValueError, match="name and description required"):
        _parse_skill(p)
