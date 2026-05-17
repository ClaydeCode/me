from pathlib import Path

import pytest

from clayde.webhook.skills import Skill, _parse_skill, discover_skills


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


def test_parse_skill_whitespace_only_name(tmp_path):
    p = _write(tmp_path / "broken.md", "---\nname: \"   \"\ndescription: real desc\n---\n\nBody.\n")
    with pytest.raises(ValueError, match="name and description required"):
        _parse_skill(p)


def test_parse_skill_whitespace_only_description(tmp_path):
    p = _write(tmp_path / "broken.md", "---\nname: real-name\ndescription: \"   \"\n---\n\nBody.\n")
    with pytest.raises(ValueError, match="name and description required"):
        _parse_skill(p)


def _write_skill(path: Path, name: str, description: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n\nbody\n")
    return path


def test_discover_recursive_alpha_order(tmp_path):
    _write_skill(tmp_path / "personal" / "b.md", "b-skill", "B")
    _write_skill(tmp_path / "personal" / "a.md", "a-skill", "A")
    _write_skill(tmp_path / "shared" / "z.md", "z-skill", "Z")
    skills = discover_skills(tmp_path)
    assert [s.name for s in skills] == ["a-skill", "b-skill", "z-skill"]


def test_discover_dedup_first_wins(tmp_path, caplog):
    a = _write_skill(tmp_path / "a" / "first.md", "dup", "first one")
    _write_skill(tmp_path / "b" / "second.md", "dup", "second one")
    with caplog.at_level("WARNING", logger="clayde.webhook"):
        skills = discover_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].path == a
    assert any("Duplicate skill name" in r.getMessage() for r in caplog.records)


def test_discover_skips_malformed(tmp_path, caplog):
    _write_skill(tmp_path / "ok.md", "ok-skill", "fine")
    (tmp_path / "broken.md").write_text("not a skill file\n")
    with caplog.at_level("WARNING", logger="clayde.webhook"):
        skills = discover_skills(tmp_path)
    assert [s.name for s in skills] == ["ok-skill"]
    assert any("Failed to parse skill" in r.getMessage() for r in caplog.records)


def test_discover_missing_root(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert discover_skills(missing) == []


from clayde.webhook.skills import build_system_prompt, build_user_prompt


def test_build_system_prompt_with_skills():
    skills = [
        Skill(name="add-note", description="Save a note.", path=Path("/skills/personal/add-note.md")),
        Skill(name="add-event", description="Create a calendar event.", path=Path("/skills/shared/cal.md")),
    ]
    prompt = build_system_prompt(skills)
    assert "Pebble watch" in prompt
    assert "speech-to-text" in prompt
    assert "phonetically similar" in prompt
    assert "- add-note: Save a note." in prompt
    assert "- add-event: Create a calendar event." in prompt
    assert "/skills/personal/add-note.md" in prompt
    assert "/skills/shared/cal.md" in prompt


def test_build_system_prompt_empty_catalog():
    prompt = build_system_prompt([])
    assert "(none currently registered)" in prompt


def test_build_user_prompt():
    out = build_user_prompt("hello world", 1778068506)
    assert "1778068506" in out
    assert "hello world" in out


def test_prompt_no_longer_caps_to_one_skill():
    from clayde.webhook.skills import Skill, build_system_prompt
    from pathlib import Path
    p = build_system_prompt([
        Skill(name="add-note", description="Save a note", path=Path("/skills/personal/add-note.md")),
        Skill(name="ping", description="Health", path=Path("/skills/builtin/ping.md")),
    ])
    assert "AT MOST ONE skill" not in p
    assert "Do not chain" not in p
    assert "as many as the command needs" in p


def test_prompt_mentions_kb_default():
    from clayde.webhook.skills import build_system_prompt
    p = build_system_prompt([])
    assert "/home/clayde/knowledge_base" in p
    assert "Syncthing" in p


def test_prompt_contains_json_contract():
    from clayde.webhook.skills import build_system_prompt
    p = build_system_prompt([])
    assert '```json' in p
    assert '"title"' in p
    assert '"body"' in p
    assert '"success"' in p


def test_prompt_when_no_skills_still_invites_judgement():
    from clayde.webhook.skills import build_system_prompt
    p = build_system_prompt([])
    assert "judgement" in p.lower() or "judgment" in p.lower()


def test_prompt_mentions_kb_structure_disambiguation():
    from clayde.webhook.skills import build_system_prompt
    p = build_system_prompt([])
    # Tells Claude to inspect KB layout and prefer phonetic neighbours
    # that match real folders ("after people and tree" → "add a people entry").
    assert "ls /home/clayde/knowledge_base" in p
    assert "phonetic" in p.lower()
    assert "people" in p


def test_discovers_builtin_alongside_host(tmp_path):
    from clayde.webhook.skills import discover_skills
    # Simulate the in-container layout: /skills/builtin + /skills/personal.
    (tmp_path / "builtin").mkdir()
    (tmp_path / "personal").mkdir()
    (tmp_path / "builtin" / "ping.md").write_text(
        "---\nname: ping\ndescription: Health check.\n---\n\npong\n"
    )
    (tmp_path / "personal" / "add-note.md").write_text(
        "---\nname: add-note\ndescription: Save a note.\n---\n\n...\n"
    )
    skills = discover_skills(tmp_path)
    names = {s.name for s in skills}
    assert names == {"ping", "add-note"}
