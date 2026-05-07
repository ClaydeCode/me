# Pebble Webhook + Skill Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FastAPI webhook to the Clayde container that receives speech-to-text messages from a Pebble watch app, and a markdown-skill framework that lets Claude pick at most one skill per request and execute it via the Claude Code CLI.

**Architecture:** Single Python process, asyncio. The existing GitHub poll loop is run in a worker thread (`asyncio.to_thread`) so it doesn't block the event loop. Uvicorn serves a FastAPI app on port 8080; an in-memory `asyncio.Queue` decouples HTTP handling from a single serial worker coroutine that invokes the Claude CLI per job. Skills are markdown files mounted under the fixed in-container path `/skills/`. Traefik runs as a separate compose service, terminates TLS via Let's Encrypt, and routes `/webhook` → `clayde:8080` over a private docker network; clayde is not attached to any externally-reachable network.

**Tech Stack:** Python 3.12+, FastAPI, uvicorn, pydantic-settings, PyYAML (frontmatter parsing), asyncio, OpenTelemetry, Claude Code CLI, Docker Compose, Traefik v3.

---

## File Structure

### New files

| Path | Purpose |
|------|---------|
| `src/clayde/webhook/__init__.py` | Package marker; re-exports `create_app`, `JobQueue`, `worker_loop`. |
| `src/clayde/webhook/auth.py` | Constant-time bearer token verification (FastAPI dependency). |
| `src/clayde/webhook/skills.py` | Skill dataclass, frontmatter parsing, recursive `/skills/` discovery, conflict logging, system-prompt builder. |
| `src/clayde/webhook/queue.py` | `PebbleJob` dataclass, `JobQueue` wrapper (in-memory `asyncio.Queue`), `QueueFullError`. |
| `src/clayde/webhook/runner.py` | `invoke_claude_pebble` — async subprocess wrapper around the Claude CLI with a fresh session per call; reuses limit/auth-error helpers from `clayde.claude`. |
| `src/clayde/webhook/worker.py` | `worker_loop` and `process_job` — pop jobs, build prompt, call runner, emit OTel `clayde.pebble.process` spans. |
| `src/clayde/webhook/app.py` | FastAPI app factory (`create_app`); `PebblePayload` model; routes `GET /health` and `POST /webhook/pebble`; OTel `clayde.pebble.enqueue` span. |
| `tests/test_webhook_skills.py` | Tests for discovery, dedup, prompt construction. |
| `tests/test_webhook_auth.py` | Tests for bearer verification. |
| `tests/test_webhook_queue.py` | Tests for enqueue + capacity. |
| `tests/test_webhook_runner.py` | Tests for runner with mocked subprocess. |
| `tests/test_webhook_app.py` | End-to-end FastAPI tests with mocked invoker. |

### Modified files

| Path | Why |
|------|-----|
| `src/clayde/config.py` | Add `pebble_*` settings fields. |
| `src/clayde/orchestrator.py` | New async entry path: when `pebble_enabled` is true, run uvicorn + worker + existing tick loop (in `to_thread`) under `asyncio.run`. |
| `pyproject.toml` | Add `fastapi`, `uvicorn[standard]`, `pyyaml` runtime deps; `pytest-asyncio`, `httpx` dev deps. |
| `docker-compose.yml` | Add `traefik` service, two networks (`web`, `internal`), routing labels and `internal` network on `clayde`, optional `~/skills/*` mounts. |
| `config.env.template` | Document new env vars. |
| `CLAUDE.md` | Document the webhook endpoint, skill format, and `/skills/` mount convention. |
| `README.md` | Brief operator section: enabling the webhook, mounting skills, Traefik setup. |

---

## Task 1: Add dependencies and Settings fields

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/clayde/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Add the failing test for new settings defaults**

Append to `tests/test_config.py`:

```python
def test_pebble_settings_defaults(monkeypatch, tmp_path):
    from clayde.config import Settings, _reset_settings
    monkeypatch.setattr("clayde.config.DATA_DIR", tmp_path)
    _reset_settings()
    s = Settings(_env_file=None)
    assert s.pebble_enabled is False
    assert s.pebble_token == ""
    assert s.pebble_port == 8080
    assert s.pebble_timeout == 600
    assert s.pebble_queue_max == 100
    assert s.pebble_host == ""
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_config.py::test_pebble_settings_defaults -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'pebble_enabled'`.

- [ ] **Step 3: Add the settings fields**

Modify `src/clayde/config.py`. Inside the `Settings` class, after `implement_max_retries: int = 3`, add:

```python
    # Pebble webhook
    pebble_enabled: bool = False
    pebble_token: str = ""
    pebble_port: int = 8080
    pebble_timeout: int = 600
    pebble_queue_max: int = 100
    pebble_host: str = ""
```

- [ ] **Step 4: Add runtime + dev dependencies**

Modify `pyproject.toml`. In `dependencies`, append (alphabetical-ish):

```toml
    "fastapi>=0.115",
    "pyyaml>=6.0",
    "uvicorn[standard]>=0.30",
```

Replace the `dev` extras line with:

```toml
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "httpx>=0.27"]
```

- [ ] **Step 5: Sync the lockfile**

Run: `uv sync`
Expected: lockfile updates; no errors.

- [ ] **Step 6: Re-run the test to confirm it passes**

Run: `uv run pytest tests/test_config.py::test_pebble_settings_defaults -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/clayde/config.py tests/test_config.py
git commit -m "feat(pebble): add settings fields and webhook dependencies"
```

---

## Task 2: Skill data model and frontmatter parser

**Files:**
- Create: `src/clayde/webhook/__init__.py` (empty package marker for now)
- Create: `src/clayde/webhook/skills.py`
- Test: `tests/test_webhook_skills.py`

- [ ] **Step 1: Create the empty package marker**

Create `src/clayde/webhook/__init__.py` with the contents:

```python
"""Pebble webhook + skill framework."""
```

- [ ] **Step 2: Write the failing test for `_parse_skill`**

Create `tests/test_webhook_skills.py` with:

```python
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
```

- [ ] **Step 3: Run the test to confirm it fails**

Run: `uv run pytest tests/test_webhook_skills.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clayde.webhook.skills'`.

- [ ] **Step 4: Implement `Skill` and `_parse_skill`**

Create `src/clayde/webhook/skills.py` with:

```python
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
    if not isinstance(name, str) or not isinstance(desc, str) or not name or not desc:
        raise ValueError(f"name and description required in frontmatter of {path}")
    return Skill(name=name.strip(), description=desc.strip(), path=path)
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `uv run pytest tests/test_webhook_skills.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/clayde/webhook/__init__.py src/clayde/webhook/skills.py tests/test_webhook_skills.py
git commit -m "feat(pebble): add Skill model and frontmatter parser"
```

---

## Task 3: Skill discovery with deterministic conflict resolution

**Files:**
- Modify: `src/clayde/webhook/skills.py`
- Test: `tests/test_webhook_skills.py`

- [ ] **Step 1: Append failing tests for `discover_skills`**

Append to `tests/test_webhook_skills.py`:

```python
from clayde.webhook.skills import discover_skills


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
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_webhook_skills.py -v`
Expected: 4 new tests FAIL with `ImportError: cannot import name 'discover_skills'`.

- [ ] **Step 3: Implement `discover_skills`**

Append to `src/clayde/webhook/skills.py`:

```python
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
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/test_webhook_skills.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/skills.py tests/test_webhook_skills.py
git commit -m "feat(pebble): recursive skill discovery with dedup and skip-on-error"
```

---

## Task 4: System-prompt builder

**Files:**
- Modify: `src/clayde/webhook/skills.py`
- Test: `tests/test_webhook_skills.py`

- [ ] **Step 1: Append failing tests for `build_system_prompt` and `build_user_prompt`**

Append to `tests/test_webhook_skills.py`:

```python
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
    assert "AT MOST ONE skill" in prompt
    assert 'respond with\nexactly "No matching skill"' in prompt or '"No matching skill"' in prompt


def test_build_system_prompt_empty_catalog():
    prompt = build_system_prompt([])
    assert "(no skills available)" in prompt
    assert 'respond with' in prompt
    assert "No matching skill" in prompt


def test_build_user_prompt():
    out = build_user_prompt("hello world", 1778068506)
    assert "1778068506" in out
    assert "hello world" in out
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_webhook_skills.py -v`
Expected: 3 new tests FAIL with `ImportError`.

- [ ] **Step 3: Implement the prompt builders**

Append to `src/clayde/webhook/skills.py`:

```python
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
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/test_webhook_skills.py -v`
Expected: PASS (all 12 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/skills.py tests/test_webhook_skills.py
git commit -m "feat(pebble): build system + user prompts for CLI invocation"
```

---

## Task 5: Bearer-token auth helper

**Files:**
- Create: `src/clayde/webhook/auth.py`
- Test: `tests/test_webhook_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_webhook_auth.py` with:

```python
import pytest
from fastapi import HTTPException

from clayde.webhook.auth import verify_bearer


def test_verify_bearer_accepts_correct_token():
    verify_bearer("Bearer secret-xyz", expected="secret-xyz")  # no exception


def test_verify_bearer_rejects_missing_header():
    with pytest.raises(HTTPException) as exc:
        verify_bearer(None, expected="secret-xyz")
    assert exc.value.status_code == 401


def test_verify_bearer_rejects_wrong_scheme():
    with pytest.raises(HTTPException) as exc:
        verify_bearer("Basic abc", expected="secret-xyz")
    assert exc.value.status_code == 401


def test_verify_bearer_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc:
        verify_bearer("Bearer wrong", expected="secret-xyz")
    assert exc.value.status_code == 401


def test_verify_bearer_rejects_empty_expected():
    # If the server is misconfigured (no token set), all requests must fail.
    with pytest.raises(HTTPException) as exc:
        verify_bearer("Bearer anything", expected="")
    assert exc.value.status_code == 401
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_webhook_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clayde.webhook.auth'`.

- [ ] **Step 3: Implement `verify_bearer`**

Create `src/clayde/webhook/auth.py` with:

```python
"""Bearer-token verification for the Pebble webhook."""

from __future__ import annotations

import secrets

from fastapi import HTTPException


def verify_bearer(authorization: str | None, *, expected: str) -> None:
    """Verify an ``Authorization: Bearer <token>`` header.

    Raises ``HTTPException(401)`` for missing header, wrong scheme,
    wrong token, or missing server-side token (misconfiguration).
    Comparison is constant-time.
    """
    if not expected:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    provided = authorization[len("Bearer "):]
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/test_webhook_auth.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/auth.py tests/test_webhook_auth.py
git commit -m "feat(pebble): bearer-token verification with constant-time compare"
```

---

## Task 6: In-memory job queue

**Files:**
- Create: `src/clayde/webhook/queue.py`
- Test: `tests/test_webhook_queue.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_webhook_queue.py` with:

```python
import asyncio

import pytest

from clayde.webhook.queue import JobQueue, PebbleJob, QueueFullError


@pytest.mark.asyncio
async def test_enqueue_and_dequeue():
    q = JobQueue(maxsize=2)
    job = PebbleJob(id="abc", text="hi", timestamp=1)
    q.enqueue(job)
    got = await q.get()
    assert got == job


@pytest.mark.asyncio
async def test_enqueue_raises_when_full():
    q = JobQueue(maxsize=1)
    q.enqueue(PebbleJob(id="a", text="", timestamp=0))
    with pytest.raises(QueueFullError):
        q.enqueue(PebbleJob(id="b", text="", timestamp=0))


@pytest.mark.asyncio
async def test_get_blocks_until_enqueued():
    q = JobQueue(maxsize=2)
    job = PebbleJob(id="abc", text="hi", timestamp=1)

    async def producer():
        await asyncio.sleep(0.01)
        q.enqueue(job)

    asyncio.create_task(producer())
    got = await asyncio.wait_for(q.get(), timeout=1.0)
    assert got == job
```

Add `pytest-asyncio` config. Append to `pyproject.toml` (after the existing `[tool.pytest.ini_options]` section's `testpaths` line):

```toml
asyncio_mode = "auto"
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_webhook_queue.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement queue module**

Create `src/clayde/webhook/queue.py` with:

```python
"""In-memory job queue for the Pebble webhook."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class QueueFullError(Exception):
    """Raised when the queue is at capacity."""


@dataclass(frozen=True)
class PebbleJob:
    id: str
    text: str
    timestamp: int


class JobQueue:
    """Thin wrapper over ``asyncio.Queue[PebbleJob]`` with non-blocking enqueue."""

    def __init__(self, maxsize: int):
        self._q: asyncio.Queue[PebbleJob] = asyncio.Queue(maxsize=maxsize)

    def enqueue(self, job: PebbleJob) -> None:
        """Non-blocking enqueue. Raises ``QueueFullError`` when full."""
        try:
            self._q.put_nowait(job)
        except asyncio.QueueFull as e:
            raise QueueFullError() from e

    async def get(self) -> PebbleJob:
        return await self._q.get()
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/test_webhook_queue.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/clayde/webhook/queue.py tests/test_webhook_queue.py
git commit -m "feat(pebble): in-memory asyncio job queue with non-blocking enqueue"
```

---

## Task 7: Async Claude CLI runner for Pebble

**Files:**
- Create: `src/clayde/webhook/runner.py`
- Test: `tests/test_webhook_runner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_webhook_runner.py` with:

```python
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from clayde.claude import InvocationTimeoutError, UsageLimitError
from clayde.webhook import runner


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.kill = MagicMock()
        self.wait = AsyncMock()

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.fixture
def fake_subproc(monkeypatch):
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return captured["proc"]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    return captured


async def test_runner_returns_result_text(fake_subproc, tmp_path):
    fake_subproc["proc"] = _FakeProc(json.dumps({"result": "all good"}).encode())
    out = await runner.invoke_claude_pebble(
        system_prompt="sys", user_text="hi", cwd=str(tmp_path), timeout_s=10,
    )
    assert out == "all good"
    cmd = fake_subproc["args"]
    assert "--append-system-prompt" in cmd
    idx = cmd.index("--append-system-prompt")
    assert cmd[idx + 1] == "sys"
    assert "-p" in cmd
    pidx = cmd.index("-p")
    assert cmd[pidx + 1] == "hi"
    assert "--resume" not in cmd
    assert "--session-id" not in cmd  # one-shot, no persistence


async def test_runner_raises_timeout(fake_subproc, tmp_path, monkeypatch):
    proc = _FakeProc(b"")

    async def slow_communicate():
        await asyncio.sleep(10)

    proc.communicate = slow_communicate
    fake_subproc["proc"] = proc

    with pytest.raises(InvocationTimeoutError):
        await runner.invoke_claude_pebble(
            system_prompt="s", user_text="t", cwd=str(tmp_path), timeout_s=0,
        )
    proc.kill.assert_called_once()


async def test_runner_raises_usage_limit_on_stderr(fake_subproc, tmp_path):
    fake_subproc["proc"] = _FakeProc(
        stdout=b"{}", stderr=b"hit your usage limit", returncode=1,
    )
    with pytest.raises(UsageLimitError):
        await runner.invoke_claude_pebble(
            system_prompt="s", user_text="t", cwd=str(tmp_path), timeout_s=10,
        )


async def test_runner_returns_no_match_unchanged(fake_subproc, tmp_path):
    fake_subproc["proc"] = _FakeProc(json.dumps({"result": "No matching skill"}).encode())
    out = await runner.invoke_claude_pebble(
        system_prompt="s", user_text="t", cwd=str(tmp_path), timeout_s=10,
    )
    assert out == "No matching skill"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run pytest tests/test_webhook_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: clayde.webhook.runner`.

- [ ] **Step 3: Implement the runner**

Create `src/clayde/webhook/runner.py` with:

```python
"""Async Pebble invocation of the Claude CLI — fresh session per call."""

from __future__ import annotations

import asyncio
import json
import logging

from clayde.claude import (
    InvocationTimeoutError,
    UsageLimitError,
    _is_auth_error,
    _is_limit_error,
    _make_cli_env,
    _resolve_cli_bin,
)

log = logging.getLogger("clayde.webhook.worker")


async def invoke_claude_pebble(
    *, system_prompt: str, user_text: str, cwd: str, timeout_s: int,
) -> str:
    """Run the Claude CLI for a single Pebble request and return its result text.

    Always a fresh session — no resume, no session-id persistence.
    Raises ``InvocationTimeoutError`` on timeout, ``UsageLimitError`` on
    rate/usage limits, ``RuntimeError`` on auth errors.
    """
    cli_bin = _resolve_cli_bin()
    cmd = [
        cli_bin,
        "-p", user_text,
        "--append-system-prompt", system_prompt,
        "--output-format", "json",
        "--dangerously-skip-permissions",
    ]
    log.info("Invoking Claude CLI (cwd=%s, timeout=%ds)", cwd, timeout_s)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=_make_cli_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s,
        )
    except asyncio.TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise InvocationTimeoutError(
            f"Claude CLI timed out after {timeout_s}s"
        ) from e

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    output_text = ""
    is_error = False
    try:
        parsed = json.loads(stdout)
        output_text = parsed.get("result", "") or ""
        is_error = bool(parsed.get("is_error", False))
    except (json.JSONDecodeError, TypeError):
        output_text = stdout

    if proc.returncode != 0 or is_error:
        error_text = stderr
        if is_error:
            error_text += " " + output_text
        if _is_limit_error(error_text):
            raise UsageLimitError("Claude CLI usage limit hit")
        if _is_auth_error(error_text):
            raise RuntimeError("Claude CLI authentication failed")
        log.error(
            "Claude CLI exited rc=%d is_error=%s stderr=%s",
            proc.returncode, is_error, stderr[:500],
        )

    return output_text
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run pytest tests/test_webhook_runner.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/runner.py tests/test_webhook_runner.py
git commit -m "feat(pebble): async Claude CLI runner with fresh session per call"
```

---

## Task 8: Worker coroutine with OTel processing span

**Files:**
- Create: `src/clayde/webhook/worker.py`
- Modify: `src/clayde/webhook/__init__.py`
- Test: `tests/test_webhook_worker.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_webhook_worker.py` with:

```python
import asyncio
from unittest.mock import AsyncMock

import pytest

from clayde.webhook.queue import JobQueue, PebbleJob
from clayde.webhook.worker import process_job, worker_loop


async def test_process_job_calls_runner(monkeypatch, tmp_path):
    monkeypatch.setattr("clayde.webhook.worker.SKILLS_ROOT", tmp_path)
    captured = {}

    async def fake_invoke(*, system_prompt, user_text, cwd, timeout_s):
        captured["system_prompt"] = system_prompt
        captured["user_text"] = user_text
        captured["cwd"] = cwd
        return "did the thing"

    monkeypatch.setattr("clayde.webhook.worker.invoke_claude_pebble", fake_invoke)

    job = PebbleJob(id="job-1", text="hello", timestamp=1778)
    await process_job(job, timeout_s=30)

    assert captured["user_text"].endswith("hello")
    assert "1778" in captured["user_text"]
    assert "Pebble watch" in captured["system_prompt"]
    # cwd should exist during the call but be cleaned up after
    assert captured["cwd"].startswith("/tmp/")


async def test_worker_loop_processes_until_cancelled(monkeypatch, tmp_path):
    monkeypatch.setattr("clayde.webhook.worker.SKILLS_ROOT", tmp_path)
    invocations = []

    async def fake_invoke(**kwargs):
        invocations.append(kwargs["user_text"])
        return ""

    monkeypatch.setattr("clayde.webhook.worker.invoke_claude_pebble", fake_invoke)
    q = JobQueue(maxsize=4)
    q.enqueue(PebbleJob(id="a", text="one", timestamp=1))
    q.enqueue(PebbleJob(id="b", text="two", timestamp=2))

    task = asyncio.create_task(worker_loop(q, timeout_s=30))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(invocations) == 2


async def test_worker_swallows_exceptions(monkeypatch, tmp_path):
    monkeypatch.setattr("clayde.webhook.worker.SKILLS_ROOT", tmp_path)

    async def fake_invoke(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("clayde.webhook.worker.invoke_claude_pebble", fake_invoke)
    q = JobQueue(maxsize=2)
    q.enqueue(PebbleJob(id="a", text="x", timestamp=0))

    task = asyncio.create_task(worker_loop(q, timeout_s=30))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Loop must have remained alive long enough to be cancelled, not crash
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_webhook_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: clayde.webhook.worker`.

- [ ] **Step 3: Implement the worker**

Create `src/clayde/webhook/worker.py` with:

```python
"""Background worker: pop jobs and invoke the Claude CLI."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time

from clayde.telemetry import get_tracer
from clayde.webhook.queue import JobQueue, PebbleJob
from clayde.webhook.runner import invoke_claude_pebble
from clayde.webhook.skills import (
    SKILLS_ROOT,
    build_system_prompt,
    build_user_prompt,
    discover_skills,
)

log = logging.getLogger("clayde.webhook.worker")


async def process_job(job: PebbleJob, *, timeout_s: int) -> None:
    """Process a single Pebble job. Records an OTel ``clayde.pebble.process`` span."""
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.pebble.process") as span:
        span.set_attribute("pebble.job_id", job.id)
        span.set_attribute("pebble.timestamp", job.timestamp)
        span.set_attribute("pebble.text", job.text)
        span.set_attribute("pebble.text_len", len(job.text))

        skills = discover_skills(SKILLS_ROOT)
        span.set_attribute("pebble.skills_available", len(skills))
        system_prompt = build_system_prompt(skills)
        user_text = build_user_prompt(job.text, job.timestamp)

        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix=f"clayde-pebble-{job.id}-") as cwd:
            try:
                output = await invoke_claude_pebble(
                    system_prompt=system_prompt,
                    user_text=user_text,
                    cwd=cwd,
                    timeout_s=timeout_s,
                )
                if output.strip() == "No matching skill":
                    span.set_attribute("pebble.skill", "none")
                span.set_attribute("pebble.success", True)
                log.info("[%s] processed (output: %d chars)", job.id, len(output))
            except Exception as e:
                span.set_attribute("pebble.success", False)
                span.set_attribute("error.type", type(e).__name__)
                span.set_attribute("error.message", str(e))
                span.record_exception(e)
                log.exception("[%s] failed: %s", job.id, e)
                raise
            finally:
                duration_ms = int((time.monotonic() - t0) * 1000)
                span.set_attribute("pebble.duration_ms", duration_ms)


async def worker_loop(queue: JobQueue, *, timeout_s: int) -> None:
    """Pop jobs from the queue and process them serially. Runs until cancelled."""
    log.info("Pebble worker loop started (timeout_s=%d)", timeout_s)
    while True:
        job = await queue.get()
        try:
            await process_job(job, timeout_s=timeout_s)
        except Exception:
            # Already logged in process_job; keep the loop alive.
            pass
```

Update `src/clayde/webhook/__init__.py`:

```python
"""Pebble webhook + skill framework."""

from clayde.webhook.queue import JobQueue, PebbleJob, QueueFullError
from clayde.webhook.worker import process_job, worker_loop

__all__ = [
    "JobQueue",
    "PebbleJob",
    "QueueFullError",
    "process_job",
    "worker_loop",
]
```

Note the `worker_loop` keyword argument in tests should match: change tests to call `worker_loop(q, timeout_s=30)`. The test file already uses that — good.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/test_webhook_worker.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/worker.py src/clayde/webhook/__init__.py tests/test_webhook_worker.py
git commit -m "feat(pebble): worker loop with OTel process span"
```

---

## Task 9: FastAPI app, routes, and enqueue span

**Files:**
- Create: `src/clayde/webhook/app.py`
- Modify: `src/clayde/webhook/__init__.py`
- Test: `tests/test_webhook_app.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_webhook_app.py` with:

```python
import pytest
from fastapi.testclient import TestClient

from clayde.webhook.app import PebblePayload, create_app
from clayde.webhook.queue import JobQueue


@pytest.fixture
def queue():
    return JobQueue(maxsize=2)


@pytest.fixture
def client(queue):
    app = create_app(queue=queue, expected_token="test-token")
    return TestClient(app)


def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_post_unknown_path_returns_404(client):
    r = client.post("/webhook/unknown", json={"text": "x", "timestamp": 1})
    assert r.status_code == 404


def test_pebble_accepts_valid_request(client, queue):
    r = client.post(
        "/webhook/pebble",
        json={"text": "hello", "timestamp": 1778068506},
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] is True
    assert "id" in body and isinstance(body["id"], str) and len(body["id"]) > 0


def test_pebble_rejects_missing_token(client):
    r = client.post(
        "/webhook/pebble",
        json={"text": "hi", "timestamp": 1},
    )
    assert r.status_code == 401


def test_pebble_rejects_wrong_token(client):
    r = client.post(
        "/webhook/pebble",
        json={"text": "hi", "timestamp": 1},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_pebble_rejects_bad_payload(client):
    r = client.post(
        "/webhook/pebble",
        json={"text": "hi"},  # missing timestamp
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 422


def test_pebble_returns_503_when_full(queue):
    # Fill the queue using a smaller capacity so 503 is reachable.
    small = JobQueue(maxsize=1)
    app = create_app(queue=small, expected_token="t")
    client = TestClient(app)
    headers = {"Authorization": "Bearer t"}
    r1 = client.post("/webhook/pebble", json={"text": "a", "timestamp": 1}, headers=headers)
    r2 = client.post("/webhook/pebble", json={"text": "b", "timestamp": 2}, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 503
    assert r2.json() == {"queued": False, "reason": "full"}
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `uv run pytest tests/test_webhook_app.py -v`
Expected: FAIL with `ModuleNotFoundError: clayde.webhook.app`.

- [ ] **Step 3: Implement the FastAPI app**

Create `src/clayde/webhook/app.py` with:

```python
"""FastAPI app, payload model, and routes for the Pebble webhook."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from clayde.telemetry import get_tracer
from clayde.webhook.auth import verify_bearer
from clayde.webhook.queue import JobQueue, PebbleJob, QueueFullError

log = logging.getLogger("clayde.webhook")


class PebblePayload(BaseModel):
    text: str
    timestamp: int


def create_app(*, queue: JobQueue, expected_token: str) -> FastAPI:
    """Build a FastAPI app bound to the given queue and bearer token."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/webhook/pebble")
    async def receive(
        payload: PebblePayload,
        authorization: str | None = Header(default=None),
    ):
        verify_bearer(authorization, expected=expected_token)

        job_id = str(uuid.uuid4())
        job = PebbleJob(id=job_id, text=payload.text, timestamp=payload.timestamp)

        tracer = get_tracer()
        with tracer.start_as_current_span("clayde.pebble.enqueue") as span:
            span.set_attribute("pebble.job_id", job_id)
            span.set_attribute("pebble.text", payload.text)
            span.set_attribute("pebble.text_len", len(payload.text))
            span.set_attribute("pebble.timestamp", payload.timestamp)
            try:
                queue.enqueue(job)
            except QueueFullError:
                span.set_attribute("http.status_code", 503)
                log.warning("[%s] queue full — rejecting", job_id)
                return JSONResponse(
                    status_code=503,
                    content={"queued": False, "reason": "full"},
                )
            span.set_attribute("http.status_code", 200)
            log.info(
                "[%s] enqueued (text_len=%d, ts=%d)",
                job_id, len(payload.text), payload.timestamp,
            )
            return {"queued": True, "id": job_id}

    return app
```

Update `src/clayde/webhook/__init__.py` to also export `create_app`:

```python
"""Pebble webhook + skill framework."""

from clayde.webhook.app import PebblePayload, create_app
from clayde.webhook.queue import JobQueue, PebbleJob, QueueFullError
from clayde.webhook.worker import process_job, worker_loop

__all__ = [
    "JobQueue",
    "PebbleJob",
    "PebblePayload",
    "QueueFullError",
    "create_app",
    "process_job",
    "worker_loop",
]
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run pytest tests/test_webhook_app.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/app.py src/clayde/webhook/__init__.py tests/test_webhook_app.py
git commit -m "feat(pebble): FastAPI app with bearer auth, queue, and OTel enqueue span"
```

---

## Task 10: Orchestrator integration — async entry point

**Files:**
- Modify: `src/clayde/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write a failing test asserting webhook is started when pebble_enabled is true**

Append to `tests/test_orchestrator.py`:

```python
def test_run_loop_without_pebble_uses_legacy_path(monkeypatch):
    """When pebble_enabled is False, run_loop must use the existing sync path."""
    from clayde import orchestrator

    calls = []
    monkeypatch.setattr(orchestrator, "main", lambda: calls.append("tick"))
    monkeypatch.setattr(orchestrator, "_shutdown", True)  # exit after first iteration

    class _S:
        loop_interval_s = 0
        pebble_enabled = False

    monkeypatch.setattr(orchestrator, "get_settings", lambda: _S())
    orchestrator.run_loop()
    # _shutdown=True from start means main() never runs; that's fine — the
    # important assertion is no exception and no asyncio.run call.


def test_run_loop_with_pebble_invokes_async_entry(monkeypatch):
    """When pebble_enabled is True, run_loop must hand off to the async entry."""
    from clayde import orchestrator

    invoked = {}

    async def fake_async_main():
        invoked["called"] = True

    monkeypatch.setattr(orchestrator, "_run_with_pebble", fake_async_main)

    class _S:
        loop_interval_s = 0
        pebble_enabled = True
        pebble_token = "x"
        pebble_port = 8080
        pebble_timeout = 10
        pebble_queue_max = 2

    monkeypatch.setattr(orchestrator, "get_settings", lambda: _S())
    orchestrator.run_loop()
    assert invoked.get("called") is True
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_orchestrator.py -v -k pebble`
Expected: FAIL — `_run_with_pebble` doesn't exist.

- [ ] **Step 3: Implement `_run_with_pebble` and dispatch from `run_loop`**

In `src/clayde/orchestrator.py`, add these imports near the top (after the existing imports):

```python
import asyncio

import uvicorn

from clayde.webhook import JobQueue, create_app, worker_loop
```

Add this function near the bottom of the file, just before `run_loop`:

```python
async def _run_with_pebble() -> None:
    """Async entry point that runs the GitHub tick loop, the Pebble webhook,
    and the Pebble worker concurrently.
    """
    setup_logging()
    settings = get_settings()
    interval = settings.loop_interval_s
    log.info(
        "Starting Clayde with Pebble webhook (port=%d, queue_max=%d)",
        settings.pebble_port, settings.pebble_queue_max,
    )

    queue = JobQueue(maxsize=settings.pebble_queue_max)
    app = create_app(queue=queue, expected_token=settings.pebble_token)
    config = uvicorn.Config(
        app, host="0.0.0.0", port=settings.pebble_port,
        log_level="info", access_log=False, lifespan="off",
    )
    server = uvicorn.Server(config)

    async def tick_loop() -> None:
        while not _shutdown:
            try:
                await asyncio.to_thread(main)
            except SystemExit:
                pass
            except Exception:
                log.exception("Unhandled error in main loop")
            for _ in range(interval):
                if _shutdown:
                    break
                await asyncio.sleep(1)

    async def worker_task() -> None:
        await worker_loop(queue, timeout_s=settings.pebble_timeout)

    await asyncio.gather(server.serve(), tick_loop(), worker_task())
```

Replace the existing `run_loop()` body with:

```python
def run_loop():
    """Run main() in a loop with a configurable sleep interval.

    This is the container entry point. When ``pebble_enabled`` is true,
    also serves the Pebble webhook + worker on the same event loop.
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    settings = get_settings()
    if settings.pebble_enabled:
        asyncio.run(_run_with_pebble())
        return

    setup_logging()
    interval = settings.loop_interval_s
    log.info("Starting Clayde loop (interval=%ds)", interval)

    while not _shutdown:
        try:
            main()
        except SystemExit:
            pass
        except Exception:
            log.exception("Unhandled error in main loop")
        if not _shutdown:
            time.sleep(interval)
```

- [ ] **Step 4: Run the orchestrator tests to confirm they pass**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: PASS (existing tests still green; new tests pass).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/clayde/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(pebble): async orchestrator entry that gates webhook on pebble_enabled"
```

---

## Task 11: Docker compose — Traefik, networks, mounts

**Files:**
- Modify: `docker-compose.yml`
- Modify: `config.env.template`
- Modify: `Dockerfile` (verify only — no change expected)

- [ ] **Step 1: Replace `docker-compose.yml` content**

Read the current `docker-compose.yml`. Replace its entire content with:

```yaml
networks:
  web:
  internal:

services:
  traefik:
    image: traefik:v3
    restart: unless-stopped
    networks: [web, internal]
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --providers.docker.network=internal
      - --entrypoints.websecure.address=:443
      - --entrypoints.web.address=:80
      - --certificatesresolvers.le.acme.email=${CLAYDE_GIT_EMAIL}
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.httpchallenge=true
      - --certificatesresolvers.le.acme.httpchallenge.entrypoint=web
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./data/letsencrypt:/letsencrypt
    labels:
      - "com.centurylinklabs.watchtower.enable=true"

  clayde:
    image: ghcr.io/claydecode/me:main
    restart: unless-stopped
    user: "1000:1000"
    networks: [internal]
    expose:
      - "8080"
    environment:
      - CLAYDE_ENABLED=true
    volumes:
      - ./data:/data
      # Mount Claude CLI OAuth credentials (required when CLAYDE_CLAUDE_BACKEND=cli)
      - ~/.claude/.credentials.json:/home/clayde/.claude/.credentials.json
      # Pebble skill directories — mount one or more host dirs read-only
      # under /skills/. Subdirectory layout is free; discovery is recursive.
      - ~/skills/personal:/skills/personal:ro
      - ~/skills/shared:/skills/shared:ro
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
      - "traefik.enable=true"
      - "traefik.http.routers.clayde.rule=Host(`${CLAYDE_PEBBLE_HOST}`) && PathPrefix(`/webhook`)"
      - "traefik.http.routers.clayde.entrypoints=websecure"
      - "traefik.http.routers.clayde.tls.certresolver=le"
      - "traefik.http.services.clayde.loadbalancer.server.port=8080"

  watchtower:
    image: containrrr/watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    command: --interval 300 --cleanup --label-enable
```

- [ ] **Step 2: Update `config.env.template`**

Read current `config.env.template`. Append:

```
# --- Pebble webhook ---
# Set to true to enable the FastAPI webhook on port 8080 (routed via Traefik).
CLAYDE_PEBBLE_ENABLED=false
# Bearer token the Pebble app sends in Authorization: Bearer <token>.
# Generate a long random string and configure it in the Pebble app's settings.
CLAYDE_PEBBLE_TOKEN=
# Public hostname for Traefik routing (e.g. clayde.example.com).
# Required when CLAYDE_PEBBLE_ENABLED=true.
CLAYDE_PEBBLE_HOST=
# Internal HTTP port (default 8080; Traefik backend target).
CLAYDE_PEBBLE_PORT=8080
# Per-request CLI timeout in seconds.
CLAYDE_PEBBLE_TIMEOUT=600
# Maximum queued Pebble jobs before 503.
CLAYDE_PEBBLE_QUEUE_MAX=100
```

- [ ] **Step 3: Verify `Dockerfile` does not need changes**

Run: `grep -n "EXPOSE\|CMD\|ENTRYPOINT" Dockerfile`
Expected: existing CMD/ENTRYPOINT runs the `clayde` script (which now dispatches based on `pebble_enabled`). No `EXPOSE` change needed — `expose: 8080` in compose covers it.

If the Dockerfile lacks an `EXPOSE 8080` line and you'd like documentation, optionally add it; otherwise leave it alone.

- [ ] **Step 4: Validate compose syntax**

Run: `docker compose config -q`
Expected: exit code 0, no output.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml config.env.template
git commit -m "feat(pebble): docker-compose with Traefik, private network, /skills mounts"
```

---

## Task 12: Manual smoke test (no commit)

**Files:** none (verification only)

- [ ] **Step 1: Build a local image**

Run: `docker compose build clayde`
Expected: image builds.

- [ ] **Step 2: Set up a test config and a minimal echo skill**

In the project root:

```bash
mkdir -p data
cp config.env.template data/config.env
# Edit data/config.env: set CLAYDE_ENABLED=false, CLAYDE_PEBBLE_ENABLED=true,
# CLAYDE_PEBBLE_TOKEN=test-token, CLAYDE_PEBBLE_HOST=localhost.

mkdir -p ~/skills/personal
cat > ~/skills/personal/echo.md <<'EOF'
---
name: echo
description: Echo the user's text into a file under /tmp/clayde-pebble-out.txt.
---

Append the user's text to `/tmp/clayde-pebble-out.txt`. Create the file
if it doesn't exist. Then respond with "echoed".
EOF
```

- [ ] **Step 3: Run only the clayde service (skip Traefik for local smoke)**

```bash
docker run --rm -it \
  -p 8080:8080 \
  -e CLAYDE_PEBBLE_ENABLED=true \
  -e CLAYDE_PEBBLE_TOKEN=test-token \
  -e CLAYDE_PEBBLE_HOST=localhost \
  -e CLAYDE_ENABLED=false \
  -v "$PWD/data:/data" \
  -v "$HOME/skills/personal:/skills/personal:ro" \
  -v "$HOME/.claude/.credentials.json:/home/clayde/.claude/.credentials.json" \
  ghcr.io/claydecode/me:local
```

Expected: log line `Starting Clayde with Pebble webhook (port=8080, queue_max=100)`.

- [ ] **Step 4: Hit `/health`**

```bash
curl -sv http://localhost:8080/health
```

Expected: `HTTP/1.1 200 OK`, body `{"ok":true}`.

- [ ] **Step 5: Hit `/webhook/pebble` with valid auth**

```bash
curl -sv -X POST http://localhost:8080/webhook/pebble \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"text":"echo hello world","timestamp":1778068506}'
```

Expected: `HTTP/1.1 200 OK`, JSON `{"queued":true,"id":"<uuid>"}`. Inside the container, `/tmp/clayde-pebble-out.txt` should eventually contain `echo hello world`.

- [ ] **Step 6: Hit `/webhook/pebble` with bad auth**

```bash
curl -sv -X POST http://localhost:8080/webhook/pebble \
  -H "Authorization: Bearer wrong" \
  -H "Content-Type: application/json" \
  -d '{"text":"x","timestamp":1}'
```

Expected: `HTTP/1.1 401`.

- [ ] **Step 7: Verify OTel spans cross-reference**

```bash
docker exec <container-id> grep clayde.pebble /data/logs/traces.jsonl | tail -2
```

Expected: at least one `clayde.pebble.enqueue` and one `clayde.pebble.process` line, both containing the same `pebble.job_id` value.

If anything fails: stop, fix, re-run the corresponding earlier task's tests.

---

## Task 13: Documentation updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md**

Open `CLAUDE.md`. Locate the `## Project Structure` section. Inside the `src/clayde/` tree listing, add the `webhook/` package after the `tasks/` package:

```
  webhook/
    __init__.py
    app.py          # FastAPI app, /webhook/pebble, /health, OTel enqueue span
    auth.py         # constant-time bearer-token verification
    queue.py        # PebbleJob, JobQueue (in-memory asyncio.Queue), QueueFullError
    runner.py       # invoke_claude_pebble — async CLI subprocess, fresh session
    skills.py       # Skill model, /skills/ discovery, system + user prompt builders
    worker.py       # worker_loop, process_job — pop jobs, OTel process span
```

Locate the `## Configuration (data/config.env)` section. Append new rows to the env-var table:

```
| `CLAYDE_PEBBLE_ENABLED` | Set to `true` to enable the Pebble webhook |
| `CLAYDE_PEBBLE_TOKEN` | Bearer token the Pebble app sends |
| `CLAYDE_PEBBLE_HOST` | Public hostname for Traefik routing |
| `CLAYDE_PEBBLE_PORT` | Internal HTTP port (default 8080) |
| `CLAYDE_PEBBLE_TIMEOUT` | Per-request CLI timeout seconds (default 600) |
| `CLAYDE_PEBBLE_QUEUE_MAX` | Max queued jobs before 503 (default 100) |
```

After the Configuration section, add a new section:

```markdown
---

## Pebble Webhook

When `CLAYDE_PEBBLE_ENABLED=true`, the container also serves a FastAPI
webhook for a Pebble watch app, alongside the existing GitHub poll loop
(both run on the same asyncio event loop).

- `POST /webhook/pebble` — accepts `{"text": str, "timestamp": int}` with
  `Authorization: Bearer <CLAYDE_PEBBLE_TOKEN>`. Returns 200 with a job id.
- `GET /health` — liveness probe (no auth).

The text is dispatched to the Claude CLI with a system prompt listing
*skills* found under the in-container path `/skills/`. Each skill is a
single markdown file with frontmatter:

\`\`\`markdown
---
name: my-skill
description: One-line description used in skill catalog.
---

(Body: instructions for Claude.)
\`\`\`

Mount one or more host directories read-only under `/skills/` in
`docker-compose.yml`. Discovery is recursive; subdirectory layout is
free. Duplicate `name` fields are logged and only the first-discovered
skill is used.

Claude must pick AT MOST ONE skill per request, or respond exactly
"No matching skill". Each request gets a fresh `claude` session — no
context carries between requests.
```

- [ ] **Step 2: Update README.md**

Open `README.md`. Add a new top-level section before any deployment
section (or at a sensible location near the existing setup docs):

```markdown
## Pebble Watch Integration

To enable receiving voice commands from a Pebble watch app:

1. Set `CLAYDE_PEBBLE_ENABLED=true` and a strong random
   `CLAYDE_PEBBLE_TOKEN` in `data/config.env`.
2. Set `CLAYDE_PEBBLE_HOST` to the public hostname Traefik should serve
   (e.g. `clayde.example.com`). The hostname must resolve to the host's
   public IP and ports 80 + 443 must be open for Let's Encrypt HTTP-01.
3. Mount one or more skill directories under `/skills/` in
   `docker-compose.yml`. Each skill is a markdown file with frontmatter
   `name` and `description`. See `CLAUDE.md` for details.
4. Configure the Pebble app to POST to
   `https://<CLAYDE_PEBBLE_HOST>/webhook/pebble` with the bearer token.

The webhook is fire-and-forget: requests return 200 with a job id and
work happens asynchronously in a single serial worker.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs(pebble): document webhook endpoint, skill format, and operator setup"
```

---

## Task 14: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -v`
Expected: ALL tests pass — both the new Pebble tests and all pre-existing
tests (especially `test_orchestrator.py`, `test_claude.py`, `test_safety.py`).

- [ ] **Step 2: Confirm no behavioural drift in the existing GitHub loop**

Run: `uv run pytest tests/test_orchestrator.py tests/test_tasks_implement.py tests/test_tasks_plan.py tests/test_tasks_review.py -v`
Expected: All pass.

- [ ] **Step 3: Confirm the legacy `clayde-once` entry point still works**

Run: `uv run python -c "from clayde.orchestrator import main; print('import ok')"`
Expected: `import ok`.

- [ ] **Step 4: Final commit (only if anything is staged)**

If there are no further changes, this is a no-op. Otherwise commit any
last fixes with a descriptive message.

---

## Self-Review Notes

- Spec coverage: every requirement from the spec is implemented by a task above:
  - Routes (200/401/422/503) → Task 9.
  - Bearer auth + constant-time → Task 5.
  - In-memory queue + 503 on full → Task 6 + Task 9.
  - Skills under `/skills/`, recursive, dedup, alpha-by-path → Task 3.
  - System prompt with phonetic-similarity hint + "at most one skill" rule → Task 4.
  - One-shot CLI (no resume), `--append-system-prompt`, scratch cwd, timeout → Task 7.
  - OTel `clayde.pebble.enqueue` and `clayde.pebble.process` cross-referenced by `pebble.job_id` → Task 8 + Task 9.
  - Config env vars → Task 1 + Task 11.
  - Docker compose with Traefik, two networks, no host port on clayde → Task 11.
  - Loop + webhook coexist (asyncio + `to_thread`) → Task 10.
  - Tests for auth/queue/skills/app → Tasks 5/6/3/4/7/8/9.
- No placeholders.
- Type consistency: `Skill`, `PebbleJob`, `JobQueue`, `QueueFullError` are
  defined once and imported by name; the runner's signature
  `invoke_claude_pebble(*, system_prompt, user_text, cwd, timeout_s)` is
  used identically by tests and the worker.
