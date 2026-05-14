# Pebble Robustness, ntfy, Multi-Skill, KB-Default — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ntfy completion notifications, lift the single-skill cap, and make `~/knowledge_base/` the default working target for the Pebble webhook.

**Architecture:** One ntfy POST per terminal outcome (success/fail) via a new `webhook/notify.py`. Claude returns a fenced JSON tail (`title`, `body`, `success`) parsed by a runner-side helper. Worker wraps every job in a single try/except that emits exactly one notification per call. Runner `cwd` switches from per-request tempdir to `CLAYDE_KB_PATH` (mounted from host `~/knowledge_base/`). A new `CliInvocationError` lets the worker distinguish CLI failure from success.

**Tech Stack:** Python 3.12+, FastAPI, asyncio, httpx (already pulled by FastAPI/uvicorn), pydantic-settings, pytest, respx (new test-only dep) for ntfy mocking.

**Spec:** `docs/superpowers/specs/2026-05-13-pebble-robustness-design.md`

**Branch:** `clayde/pebble-robustness` (worktree at `.worktrees/pebble-robustness/`)

---

## File Map

**Create:**
- `src/clayde/webhook/notify.py` — `send_ntfy`, `NotificationPayload`
- `src/clayde/skills_builtin/ping.md` — built-in health-check skill
- `tests/test_webhook_notify.py` — notify unit tests
- `tests/test_webhook_runner_parse.py` — JSON-tail parser tests
- `tests/test_pebble_e2e.py` — end-to-end integration test

**Modify:**
- `src/clayde/config.py` — new Settings fields
- `src/clayde/claude.py` — add `CliInvocationError`
- `src/clayde/webhook/runner.py` — raise `CliInvocationError` on rc!=0; add `extract_notification_payload`
- `src/clayde/webhook/skills.py` — new system prompt (multi-skill, KB default, JSON contract)
- `src/clayde/webhook/worker.py` — single try/except, send_ntfy each terminal branch, KB cwd, `pebble.outcome` enum
- `src/clayde/webhook/app.py` — send_ntfy on QueueFull
- `src/clayde/webhook/__init__.py` — re-export new symbols
- `Dockerfile` — `COPY skills_builtin /skills/builtin/`
- `docker-compose.yml` — KB volume mount
- `config.env.template` — new env vars
- `CLAUDE.md`, `README.md` — KB default, multi-skill, ntfy
- `pyproject.toml` — add `respx` as test dep
- `tests/test_webhook_skills.py`, `tests/test_webhook_worker.py`, `tests/test_webhook_app.py`, `tests/test_webhook_runner.py`, `tests/test_config.py` — update for new behavior

---

## Task 1: Config additions and timeout default bump

**Files:**
- Modify: `src/clayde/config.py:46-51` (Pebble webhook section)
- Modify: `tests/test_config.py`
- Modify: `config.env.template`

- [ ] **Step 1: Write failing test for new settings**

Append to `tests/test_config.py`:

```python
def test_pebble_timeout_default_is_300():
    from clayde.config import Settings
    s = Settings(_env_file=None)
    assert s.pebble_timeout == 300


def test_ntfy_defaults():
    from clayde.config import Settings
    s = Settings(_env_file=None)
    assert s.ntfy_topic == "7yuau0vyes"
    assert s.ntfy_base_url == "https://ntfy.sh"
    assert s.ntfy_timeout_s == 10


def test_kb_path_default():
    from clayde.config import Settings
    s = Settings(_env_file=None)
    assert s.kb_path == "/home/clayde/knowledge_base"
```

- [ ] **Step 2: Run tests, see them fail**

```
uv run pytest tests/test_config.py -v
```

Expected: 3 new tests FAIL with `AttributeError` (settings don't exist) or `assert 600 == 300`.

- [ ] **Step 3: Add new settings**

Edit `src/clayde/config.py` Pebble section (replace `pebble_timeout: int = 600`):

```python
    # Pebble webhook
    pebble_enabled: bool = False
    pebble_token: str = ""
    pebble_port: int = 8080
    pebble_timeout: int = 300
    pebble_queue_max: int = 100
    pebble_host: str = ""

    # ntfy notifications (Pebble outcome feedback)
    ntfy_topic: str = "7yuau0vyes"
    ntfy_base_url: str = "https://ntfy.sh"
    ntfy_timeout_s: int = 10

    # Knowledge base (default cwd for Pebble runs)
    kb_path: str = "/home/clayde/knowledge_base"
```

- [ ] **Step 4: Run tests, see them pass**

```
uv run pytest tests/test_config.py -v
```

Expected: all PASS.

- [ ] **Step 5: Update config.env.template**

Append to `config.env.template` (after the existing Pebble block):

```
# --- ntfy notifications (Pebble outcome feedback) ---
# Default topic is public on ntfy.sh; anyone with the string can read transcripts.
CLAYDE_NTFY_TOPIC=7yuau0vyes
CLAYDE_NTFY_BASE_URL=https://ntfy.sh
CLAYDE_NTFY_TIMEOUT_S=10

# --- Knowledge base (default cwd for Pebble runs) ---
# Mounted from host ~/knowledge_base/. Synced by Syncthing — no git in container.
CLAYDE_KB_PATH=/home/clayde/knowledge_base
```

Also bump the existing `CLAYDE_PEBBLE_TIMEOUT` comment to mention `300` is the new default.

- [ ] **Step 6: Commit**

```bash
git add src/clayde/config.py tests/test_config.py config.env.template
git commit -m "feat(pebble): config for ntfy + kb_path; lower pebble_timeout default to 300"
```

---

## Task 2: CliInvocationError + runner raises on non-zero exit

**Files:**
- Modify: `src/clayde/claude.py` (add exception class)
- Modify: `src/clayde/webhook/runner.py:78-91` (return → raise on rc!=0)
- Modify: `tests/test_webhook_runner.py`

- [ ] **Step 1: Write failing test for CliInvocationError on non-zero exit**

Append to `tests/test_webhook_runner.py`:

```python
import json

import pytest

from clayde.claude import CliInvocationError
from clayde.webhook import runner


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return (self._stdout, self._stderr)

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_runner_raises_cli_invocation_error_on_nonzero(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(returncode=2, stdout=b'{"result": "boom"}', stderr=b"boom on stderr")

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(runner, "_resolve_cli_bin", lambda: "/bin/true")
    monkeypatch.setattr(runner, "_make_cli_env", lambda: {})

    with pytest.raises(CliInvocationError) as exc:
        await runner.invoke_claude_pebble(
            system_prompt="sys", user_text="hi", cwd="/tmp", timeout_s=5,
        )
    assert "boom" in exc.value.stderr


@pytest.mark.asyncio
async def test_runner_returns_text_on_zero_exit(monkeypatch):
    payload = json.dumps({"result": "ok"}).encode()
    async def fake_exec(*args, **kwargs):
        return _FakeProc(returncode=0, stdout=payload, stderr=b"")

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(runner, "_resolve_cli_bin", lambda: "/bin/true")
    monkeypatch.setattr(runner, "_make_cli_env", lambda: {})

    out = await runner.invoke_claude_pebble(
        system_prompt="sys", user_text="hi", cwd="/tmp", timeout_s=5,
    )
    assert out == "ok"
```

- [ ] **Step 2: Run tests, see them fail**

```
uv run pytest tests/test_webhook_runner.py::test_runner_raises_cli_invocation_error_on_nonzero -v
```

Expected: FAIL with `ImportError: cannot import name 'CliInvocationError'` or, after that's fixed, the test fails because the runner currently logs and returns instead of raising.

- [ ] **Step 3: Add the exception class to clayde/claude.py**

In `src/clayde/claude.py`, near the existing `UsageLimitError` / `InvocationTimeoutError` definitions, add:

```python
class CliInvocationError(Exception):
    """Raised when the Claude CLI exits non-zero and the error is not
    recognized as an auth failure or usage-limit hit.

    Attributes:
        stderr: tail of the CLI's stderr (truncated, for safe display).
    """

    def __init__(self, stderr: str):
        self.stderr = stderr
        super().__init__(stderr[:200] if stderr else "claude CLI exited non-zero")
```

- [ ] **Step 4: Modify runner to raise CliInvocationError on rc!=0**

Edit `src/clayde/webhook/runner.py`. Replace the import block at lines 9-16:

```python
from clayde.claude import (
    CliInvocationError,
    InvocationTimeoutError,
    UsageLimitError,
    _is_auth_error,
    _is_limit_error,
    _make_cli_env,
    _resolve_cli_bin,
)
```

Replace the final block at lines 78-91 (the `if proc.returncode != 0 or is_error:` clause and the `return output_text`):

```python
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
        raise CliInvocationError(stderr or output_text)

    return output_text
```

(Only one line changed materially: the trailing `raise CliInvocationError(...)`.)

- [ ] **Step 5: Update the runner's docstring**

Edit `invoke_claude_pebble` docstring (around line 24) to mention the new exception:

```python
    """Run the Claude CLI for a single Pebble request and return its result text.

    Always a fresh session — no resume, no session-id persistence.
    Raises ``InvocationTimeoutError`` on timeout, ``UsageLimitError`` on
    rate/usage limits, ``RuntimeError`` on auth errors, ``CliInvocationError``
    on any other non-zero exit.
    """
```

- [ ] **Step 6: Run tests, see them pass**

```
uv run pytest tests/test_webhook_runner.py -v
```

Expected: PASS for both new tests, all existing runner tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add src/clayde/claude.py src/clayde/webhook/runner.py tests/test_webhook_runner.py
git commit -m "feat(pebble): raise CliInvocationError on CLI non-zero exit"
```

---

## Task 3: notify module — send_ntfy + NotificationPayload

**Files:**
- Create: `src/clayde/webhook/notify.py`
- Create: `tests/test_webhook_notify.py`
- Modify: `pyproject.toml` (add `respx` test dep)
- Modify: `src/clayde/webhook/__init__.py` (re-export)

- [ ] **Step 1: Add respx to test deps**

Edit `pyproject.toml`. In the test/dev optional dependency group (alongside `pytest`, `pytest-asyncio`, etc.), add `"respx"`.

```bash
uv sync --all-extras
```

- [ ] **Step 2: Write failing tests for notify**

Create `tests/test_webhook_notify.py`:

```python
"""Tests for the ntfy notification dispatcher."""

from __future__ import annotations

import httpx
import pytest
import respx

from clayde.webhook.notify import NotificationPayload, send_ntfy


def test_notification_payload_clamps_length():
    p = NotificationPayload(title="x" * 100, body="y" * 1000, success=True)
    assert len(p.title) == 40
    assert len(p.body) == 300


def test_notification_payload_accepts_short():
    p = NotificationPayload(title="hi", body="all good", success=True)
    assert p.title == "hi"
    assert p.body == "all good"
    assert p.success is True


@pytest.mark.asyncio
@respx.mock
async def test_send_ntfy_success_headers():
    route = respx.post("https://ntfy.sh/abc123").mock(
        return_value=httpx.Response(200, json={"id": "msg1"})
    )
    await send_ntfy(
        title="pong",
        body="alive",
        success=True,
        base_url="https://ntfy.sh",
        topic="abc123",
        timeout_s=5,
    )
    assert route.called
    req = route.calls.last.request
    assert req.headers["title"] == "pong"
    assert req.headers["priority"] == "3"
    assert req.headers["tags"] == "white_check_mark"
    assert req.content == b"alive"


@pytest.mark.asyncio
@respx.mock
async def test_send_ntfy_failure_headers():
    route = respx.post("https://ntfy.sh/abc123").mock(
        return_value=httpx.Response(200, json={"id": "msg1"})
    )
    await send_ntfy(
        title="Pebble: timeout",
        body="ran 300s+",
        success=False,
        base_url="https://ntfy.sh",
        topic="abc123",
        timeout_s=5,
    )
    req = route.calls.last.request
    assert req.headers["priority"] == "5"
    assert req.headers["tags"] == "rotating_light"


@pytest.mark.asyncio
@respx.mock
async def test_send_ntfy_swallows_errors():
    respx.post("https://ntfy.sh/abc123").mock(side_effect=httpx.ConnectError("nope"))
    # Must not raise.
    await send_ntfy(
        title="ok",
        body="ok",
        success=True,
        base_url="https://ntfy.sh",
        topic="abc123",
        timeout_s=5,
    )
```

- [ ] **Step 3: Run tests, see them fail**

```
uv run pytest tests/test_webhook_notify.py -v
```

Expected: FAIL with `ModuleNotFoundError: clayde.webhook.notify`.

- [ ] **Step 4: Implement notify module**

Create `src/clayde/webhook/notify.py`:

```python
"""ntfy.sh notification dispatch for Pebble webhook terminal outcomes.

Best-effort: any HTTP / network failure is logged but never raised.
A notification is feedback, not a transactional side-effect.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel, Field

from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.webhook.notify")


class NotificationPayload(BaseModel):
    """Outcome of a Pebble run, as emitted by Claude in the JSON tail."""

    title: str = Field(..., max_length=40)
    body: str = Field(..., max_length=300)
    success: bool

    @classmethod
    def model_validate_lenient(cls, data: dict) -> "NotificationPayload":
        """Construct, clamping title/body to max length instead of erroring."""
        return cls(
            title=str(data.get("title", ""))[:40] or "Pebble: done",
            body=str(data.get("body", ""))[:300] or "(no body)",
            success=bool(data.get("success", False)),
        )


# pydantic's built-in max_length raises by default; we want truncation.
# Override the constructor to clamp before validation.
def _clamp(value: str, n: int) -> str:
    return value[:n] if isinstance(value, str) else value


def _clamping_init(self, **data):  # type: ignore[no-redef]
    if "title" in data:
        data["title"] = _clamp(data["title"], 40)
    if "body" in data:
        data["body"] = _clamp(data["body"], 300)
    BaseModel.__init__(self, **data)


NotificationPayload.__init__ = _clamping_init  # type: ignore[assignment]


async def send_ntfy(
    *,
    title: str,
    body: str,
    success: bool,
    base_url: str,
    topic: str,
    timeout_s: int,
) -> None:
    """POST to ntfy.sh. Best-effort: errors are logged + OTel-annotated, never raised."""
    url = f"{base_url.rstrip('/')}/{topic}"
    headers = {
        "Title": title,
        "Priority": "3" if success else "5",
        "Tags": "white_check_mark" if success else "rotating_light",
    }
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.pebble.notify") as span:
        span.set_attribute("notify.topic", topic)
        span.set_attribute("notify.title", title)
        span.set_attribute("notify.success_flag", success)
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, content=body, headers=headers)
            span.set_attribute("notify.http_status", resp.status_code)
            span.set_attribute("notify.success", 200 <= resp.status_code < 300)
            if resp.status_code >= 400:
                log.warning("ntfy returned %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            span.set_attribute("notify.success", False)
            span.set_attribute("notify.error", type(exc).__name__)
            log.warning("ntfy POST failed: %s", exc)
```

- [ ] **Step 5: Re-export from `webhook/__init__.py`**

Edit `src/clayde/webhook/__init__.py`. Add to the imports/exports:

```python
from clayde.webhook.notify import NotificationPayload, send_ntfy
```

Append `"NotificationPayload"` and `"send_ntfy"` to `__all__` if it exists.

- [ ] **Step 6: Run tests, see them pass**

```
uv run pytest tests/test_webhook_notify.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/clayde/webhook/notify.py src/clayde/webhook/__init__.py tests/test_webhook_notify.py
git commit -m "feat(pebble): ntfy notification dispatcher (best-effort)"
```

---

## Task 4: JSON-tail parser — extract_notification_payload

**Files:**
- Modify: `src/clayde/webhook/runner.py` (add helper at end of file)
- Create: `tests/test_webhook_runner_parse.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_webhook_runner_parse.py`:

```python
"""Tests for extract_notification_payload — last-JSON-block extractor."""

from __future__ import annotations

from clayde.webhook.notify import NotificationPayload
from clayde.webhook.runner import extract_notification_payload


def test_extracts_last_json_block():
    result = '''
    Working on it.
    ```json
    {"title": "stale", "body": "old", "success": false}
    ```
    Done.
    ```json
    {"title": "saved", "body": "wrote inbox/x.md", "success": true}
    ```
    '''
    p = extract_notification_payload(result)
    assert p.title == "saved"
    assert p.body == "wrote inbox/x.md"
    assert p.success is True


def test_fallback_on_missing_block():
    result = "I did things but forgot the JSON."
    p = extract_notification_payload(result)
    assert p.title == "Pebble: done (no summary)"
    assert p.body == "I did things but forgot the JSON."
    assert p.success is True


def test_fallback_on_malformed_json():
    result = "stuff\n```json\n{not valid json}\n```"
    p = extract_notification_payload(result)
    assert p.title == "Pebble: done (no summary)"
    assert p.success is True


def test_clamps_overlong_fields():
    long = "x" * 500
    result = f'```json\n{{"title": "{long}", "body": "{long}", "success": true}}\n```'
    p = extract_notification_payload(result)
    assert len(p.title) == 40
    assert len(p.body) == 300


def test_success_false_honored():
    result = '```json\n{"title": "fail", "body": "couldn\'t do it", "success": false}\n```'
    p = extract_notification_payload(result)
    assert p.success is False
```

- [ ] **Step 2: Run tests, see them fail**

```
uv run pytest tests/test_webhook_runner_parse.py -v
```

Expected: FAIL with `ImportError: cannot import name 'extract_notification_payload'`.

- [ ] **Step 3: Add the helper to runner.py**

Append to `src/clayde/webhook/runner.py`:

```python
import re

from clayde.webhook.notify import NotificationPayload

_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def extract_notification_payload(result: str) -> NotificationPayload:
    """Extract the last fenced ```json``` block from Claude's result.

    Falls back to a synthetic "no summary" payload if the block is missing
    or malformed — the run completed, only the summary is lost.
    """
    matches = list(_JSON_BLOCK_RE.finditer(result))
    if matches:
        try:
            data = json.loads(matches[-1].group(1))
            if isinstance(data, dict):
                return NotificationPayload(
                    title=str(data.get("title", "Pebble: done")),
                    body=str(data.get("body", "(no body)")),
                    success=bool(data.get("success", True)),
                )
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return NotificationPayload(
        title="Pebble: done (no summary)",
        body=result[:300] if result else "(empty output)",
        success=True,
    )
```

(Note: `json` is already imported at the top of `runner.py`.)

- [ ] **Step 4: Run tests, see them pass**

```
uv run pytest tests/test_webhook_runner_parse.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/runner.py tests/test_webhook_runner_parse.py
git commit -m "feat(pebble): extract_notification_payload — last-JSON-block parser with fallback"
```

---

## Task 5: New system prompt — multi-skill, KB default, JSON contract

**Files:**
- Modify: `src/clayde/webhook/skills.py:44-73` (template + builder)
- Modify: `tests/test_webhook_skills.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_webhook_skills.py`:

```python
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
    # Without skills, Claude should still answer / capture, not say "no matching skill".
    assert "judgement" in p.lower() or "judgment" in p.lower()
```

- [ ] **Step 2: Run tests, see them fail**

```
uv run pytest tests/test_webhook_skills.py -v
```

Expected: 4 new tests FAIL — prompt still contains "AT MOST ONE skill".

- [ ] **Step 3: Replace template and builder**

Edit `src/clayde/webhook/skills.py`. Replace lines 44-73 entirely:

```python
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
```

(Note: doubled `{{` / `}}` around the JSON example because the template uses `.format()`.)

- [ ] **Step 4: Run tests, see them pass**

```
uv run pytest tests/test_webhook_skills.py -v
```

Expected: all PASS. Existing skill-discovery tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/skills.py tests/test_webhook_skills.py
git commit -m "feat(pebble): new system prompt — multi-skill, KB default, JSON tail contract"
```

---

## Task 6: Built-in ping skill

**Files:**
- Create: `src/clayde/skills_builtin/ping.md`
- Modify: `Dockerfile`
- Modify: `tests/test_webhook_skills.py` (discovery test for builtin path)

- [ ] **Step 1: Write failing discovery test**

Append to `tests/test_webhook_skills.py`:

```python
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
```

- [ ] **Step 2: Run test, see it fail**

```
uv run pytest tests/test_webhook_skills.py::test_discovers_builtin_alongside_host -v
```

Expected: PASS already (recursive discovery exists). If it passes, that's fine — the test pins the behavior. Continue.

- [ ] **Step 3: Create the built-in ping skill**

Create `src/clayde/skills_builtin/ping.md`:

```markdown
---
name: ping
description: Health check. Use when the user says "ping", "are you there", or "test".
---

Respond with a friendly pong.

Do not write any files. Do not perform any other action.

For the notification JSON tail, set:
- title: "pong"
- body: container uptime if you can read `/proc/uptime` (format: "up Xh Ym"),
  otherwise "alive"
- success: true
```

- [ ] **Step 4: Update Dockerfile to COPY the builtin skills**

Read `Dockerfile` first:

```bash
cat Dockerfile
```

Find the line that copies the `src/` tree (typically `COPY . /opt/clayde` or `COPY src/ /opt/clayde/src/`). Immediately after, add:

```dockerfile
COPY src/clayde/skills_builtin/ /skills/builtin/
```

This bakes ping (and any future built-ins) into the image at the same path skill discovery walks.

- [ ] **Step 5: Verify all skill tests still pass**

```
uv run pytest tests/test_webhook_skills.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/clayde/skills_builtin/ping.md Dockerfile tests/test_webhook_skills.py
git commit -m "feat(pebble): built-in ping skill baked into image at /skills/builtin/"
```

---

## Task 7: Worker rewrite — single try/except, send_ntfy each branch, KB cwd, outcome enum

**Files:**
- Modify: `src/clayde/webhook/worker.py` (full rewrite of `process_job`)
- Modify: `tests/test_webhook_worker.py` (assert ntfy fires on every branch)

- [ ] **Step 1: Write failing tests covering every terminal branch**

Replace the contents of `tests/test_webhook_worker.py` (keep any existing imports + fixtures, then add):

```python
"""Worker behavior: every terminal branch must emit exactly one send_ntfy call."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from clayde.claude import (
    CliInvocationError,
    InvocationTimeoutError,
    UsageLimitError,
)
from clayde.webhook import worker
from clayde.webhook.notify import NotificationPayload
from clayde.webhook.queue import PebbleJob


@dataclass
class _NtfyCall:
    title: str
    body: str
    success: bool


@pytest.fixture
def captured_ntfy(monkeypatch):
    calls: list[_NtfyCall] = []

    async def fake_send(*, title, body, success, **_):
        calls.append(_NtfyCall(title=title, body=body, success=success))

    monkeypatch.setattr(worker, "send_ntfy", fake_send)
    return calls


@pytest.fixture
def fake_skills(monkeypatch):
    monkeypatch.setattr(worker, "discover_skills", lambda root=None: [])
    monkeypatch.setattr(worker, "build_system_prompt", lambda skills: "SYS")
    monkeypatch.setattr(worker, "build_user_prompt", lambda text, ts: f"USER:{text}")


def _job():
    return PebbleJob(id="job-1", text="hello", timestamp=1000)


@pytest.mark.asyncio
async def test_success_path_emits_one_success_ntfy(monkeypatch, captured_ntfy, fake_skills):
    async def fake_invoke(**kwargs):
        return '```json\n{"title": "saved", "body": "wrote inbox/x.md", "success": true}\n```'

    monkeypatch.setattr(worker, "invoke_claude_pebble", fake_invoke)

    await worker.process_job(_job(), timeout_s=10, kb_path="/tmp")
    assert len(captured_ntfy) == 1
    assert captured_ntfy[0].title == "saved"
    assert captured_ntfy[0].success is True


@pytest.mark.asyncio
async def test_claude_reports_failure_via_json(monkeypatch, captured_ntfy, fake_skills):
    async def fake_invoke(**kwargs):
        return '```json\n{"title": "could not", "body": "no calendar set up", "success": false}\n```'

    monkeypatch.setattr(worker, "invoke_claude_pebble", fake_invoke)
    await worker.process_job(_job(), timeout_s=10, kb_path="/tmp")
    assert len(captured_ntfy) == 1
    assert captured_ntfy[0].success is False
    assert captured_ntfy[0].title == "could not"


@pytest.mark.asyncio
async def test_parse_fallback_on_missing_json(monkeypatch, captured_ntfy, fake_skills):
    async def fake_invoke(**kwargs):
        return "I did things but forgot the JSON."

    monkeypatch.setattr(worker, "invoke_claude_pebble", fake_invoke)
    await worker.process_job(_job(), timeout_s=10, kb_path="/tmp")
    assert len(captured_ntfy) == 1
    assert captured_ntfy[0].title == "Pebble: done (no summary)"
    assert captured_ntfy[0].success is True


@pytest.mark.asyncio
async def test_timeout_emits_fail_ntfy(monkeypatch, captured_ntfy, fake_skills):
    async def fake_invoke(**kwargs):
        raise InvocationTimeoutError("ran 10s+")

    monkeypatch.setattr(worker, "invoke_claude_pebble", fake_invoke)
    await worker.process_job(_job(), timeout_s=10, kb_path="/tmp")
    assert len(captured_ntfy) == 1
    assert captured_ntfy[0].title == "Pebble: timeout"
    assert captured_ntfy[0].success is False


@pytest.mark.asyncio
async def test_usage_limit_emits_rate_limited_ntfy(monkeypatch, captured_ntfy, fake_skills):
    async def fake_invoke(**kwargs):
        raise UsageLimitError("limit hit")

    monkeypatch.setattr(worker, "invoke_claude_pebble", fake_invoke)
    await worker.process_job(_job(), timeout_s=10, kb_path="/tmp")
    assert len(captured_ntfy) == 1
    assert captured_ntfy[0].title == "Pebble: rate-limited"
    assert captured_ntfy[0].success is False


@pytest.mark.asyncio
async def test_cli_invocation_error_emits_fail_ntfy(monkeypatch, captured_ntfy, fake_skills):
    async def fake_invoke(**kwargs):
        raise CliInvocationError("stderr tail here")

    monkeypatch.setattr(worker, "invoke_claude_pebble", fake_invoke)
    await worker.process_job(_job(), timeout_s=10, kb_path="/tmp")
    assert len(captured_ntfy) == 1
    assert captured_ntfy[0].title == "Pebble: failed"
    assert "stderr tail" in captured_ntfy[0].body
    assert captured_ntfy[0].success is False


@pytest.mark.asyncio
async def test_auth_error_emits_auth_ntfy(monkeypatch, captured_ntfy, fake_skills):
    async def fake_invoke(**kwargs):
        raise RuntimeError("Claude CLI authentication failed")

    monkeypatch.setattr(worker, "invoke_claude_pebble", fake_invoke)
    await worker.process_job(_job(), timeout_s=10, kb_path="/tmp")
    assert len(captured_ntfy) == 1
    assert captured_ntfy[0].title == "Pebble: auth error"
    assert captured_ntfy[0].success is False


@pytest.mark.asyncio
async def test_unexpected_exception_emits_fail_ntfy(monkeypatch, captured_ntfy, fake_skills):
    async def fake_invoke(**kwargs):
        raise ValueError("something weird")

    monkeypatch.setattr(worker, "invoke_claude_pebble", fake_invoke)
    await worker.process_job(_job(), timeout_s=10, kb_path="/tmp")
    assert len(captured_ntfy) == 1
    assert captured_ntfy[0].title == "Pebble: failed"
    assert "ValueError" in captured_ntfy[0].body
    assert captured_ntfy[0].success is False
```

- [ ] **Step 2: Run tests, see them fail**

```
uv run pytest tests/test_webhook_worker.py -v
```

Expected: FAIL — `process_job` doesn't accept `kb_path`, doesn't call `send_ntfy`.

- [ ] **Step 3: Rewrite worker.py**

Replace `src/clayde/webhook/worker.py` entirely:

```python
"""Background worker: pop jobs, invoke the Claude CLI, emit ntfy on every outcome."""

from __future__ import annotations

import logging
import time

from clayde.claude import (
    CliInvocationError,
    InvocationTimeoutError,
    UsageLimitError,
)
from clayde.config import get_settings
from clayde.telemetry import get_tracer
from clayde.webhook.notify import send_ntfy
from clayde.webhook.queue import JobQueue, PebbleJob
from clayde.webhook.runner import extract_notification_payload, invoke_claude_pebble
from clayde.webhook.skills import (
    SKILLS_ROOT,
    build_system_prompt,
    build_user_prompt,
    discover_skills,
)

log = logging.getLogger("clayde.webhook.worker")


def _tail(s: str, n: int) -> str:
    """Return the last n characters of s, or "" if empty."""
    if not s:
        return ""
    return s[-n:]


async def _notify(*, title: str, body: str, success: bool) -> None:
    settings = get_settings()
    await send_ntfy(
        title=title,
        body=body,
        success=success,
        base_url=settings.ntfy_base_url,
        topic=settings.ntfy_topic,
        timeout_s=settings.ntfy_timeout_s,
    )


async def process_job(job: PebbleJob, *, timeout_s: int, kb_path: str) -> None:
    """Process a single Pebble job. Emits exactly one ntfy notification."""
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
        outcome = "worker_error"
        try:
            output = await invoke_claude_pebble(
                system_prompt=system_prompt,
                user_text=user_text,
                cwd=kb_path,
                timeout_s=timeout_s,
            )
            payload = extract_notification_payload(output)
            outcome = (
                "success" if payload.success and payload.title != "Pebble: done (no summary)"
                else "parse_fallback" if payload.title == "Pebble: done (no summary)"
                else "claude_fail"
            )
            await _notify(title=payload.title, body=payload.body, success=payload.success)
            log.info("[%s] processed outcome=%s", job.id, outcome)
        except InvocationTimeoutError:
            outcome = "timeout"
            log.warning("[%s] timeout", job.id)
            await _notify(
                title="Pebble: timeout",
                body=f"ran {timeout_s}s+",
                success=False,
            )
        except UsageLimitError:
            outcome = "rate_limited"
            log.warning("[%s] usage limit hit", job.id)
            await _notify(
                title="Pebble: rate-limited",
                body="try again later",
                success=False,
            )
        except CliInvocationError as exc:
            outcome = "cli_error"
            log.error("[%s] CLI error: %s", job.id, exc.stderr[:200])
            await _notify(
                title="Pebble: failed",
                body=_tail(exc.stderr, 300) or "claude CLI exited non-zero",
                success=False,
            )
        except RuntimeError as exc:
            # Auth failures raise RuntimeError from the existing runner.
            outcome = "cli_error"
            log.error("[%s] auth error: %s", job.id, exc)
            await _notify(
                title="Pebble: auth error",
                body=str(exc)[:300],
                success=False,
            )
        except Exception as exc:
            outcome = "worker_error"
            log.exception("[%s] worker error", job.id)
            await _notify(
                title="Pebble: failed",
                body=f"{type(exc).__name__}: {str(exc)[:240]}",
                success=False,
            )
        finally:
            duration_ms = int((time.monotonic() - t0) * 1000)
            span.set_attribute("pebble.duration_ms", duration_ms)
            span.set_attribute("pebble.outcome", outcome)
            span.set_attribute("pebble.success", outcome == "success")


async def worker_loop(queue: JobQueue, *, timeout_s: int, kb_path: str) -> None:
    """Pop jobs from the queue and process them serially. Runs until cancelled."""
    log.info(
        "Pebble worker loop started (timeout_s=%d, kb_path=%s)", timeout_s, kb_path,
    )
    while True:
        job = await queue.get()
        try:
            await process_job(job, timeout_s=timeout_s, kb_path=kb_path)
        except Exception:
            # process_job already emitted a notification + logged.
            pass
```

Notable: `cwd=kb_path` (not a tempdir); `process_job` accepts `kb_path`; `worker_loop` accepts and passes it.

- [ ] **Step 4: Update the orchestrator caller of `worker_loop`**

In `src/clayde/orchestrator.py` find `async def worker_task` (around line 392) and update:

```python
    async def worker_task() -> None:
        await worker_loop(
            queue,
            timeout_s=settings.pebble_timeout,
            kb_path=settings.kb_path,
        )
```

- [ ] **Step 5: Run worker tests, see them pass**

```
uv run pytest tests/test_webhook_worker.py -v
```

Expected: 8 tests PASS.

- [ ] **Step 6: Run the full suite to catch regressions**

```
uv run pytest
```

Expected: all PASS. (Existing GitHub-loop tests unaffected.)

- [ ] **Step 7: Commit**

```bash
git add src/clayde/webhook/worker.py src/clayde/orchestrator.py tests/test_webhook_worker.py
git commit -m "feat(pebble): worker emits ntfy on every terminal outcome; KB cwd; outcome enum"
```

---

## Task 8: FastAPI app emits ntfy on QueueFull

**Files:**
- Modify: `src/clayde/webhook/app.py:48-56` (QueueFull branch)
- Modify: `tests/test_webhook_app.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_webhook_app.py`:

```python
@pytest.mark.asyncio
async def test_queue_full_emits_ntfy(monkeypatch):
    from clayde.webhook import app as app_mod
    from clayde.webhook.queue import JobQueue, QueueFullError

    calls = []

    async def fake_send(*, title, body, success, **_):
        calls.append((title, success))

    monkeypatch.setattr(app_mod, "send_ntfy", fake_send)

    class FullQueue(JobQueue):
        def enqueue(self, job):
            raise QueueFullError()

    q = FullQueue(maxsize=1)
    application = app_mod.create_app(queue=q, expected_token="tok")

    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/webhook/pebble",
            json={"text": "hi", "timestamp": 1},
            headers={"Authorization": "Bearer tok"},
        )
    assert resp.status_code == 503
    assert len(calls) == 1
    assert calls[0][0] == "Pebble: queue full"
    assert calls[0][1] is False
```

- [ ] **Step 2: Run test, see it fail**

```
uv run pytest tests/test_webhook_app.py::test_queue_full_emits_ntfy -v
```

Expected: FAIL — `send_ntfy` not in `app_mod` namespace, no ntfy call captured.

- [ ] **Step 3: Wire send_ntfy into the QueueFull branch**

Edit `src/clayde/webhook/app.py`. At the top, add the import:

```python
from clayde.config import get_settings
from clayde.webhook.notify import send_ntfy
```

Replace the `QueueFullError` handler (around lines 50-56):

```python
            except QueueFullError:
                span.set_attribute("http.status_code", 503)
                log.warning("[%s] queue full — rejecting", job_id)
                settings = get_settings()
                await send_ntfy(
                    title="Pebble: queue full",
                    body=f"text: {payload.text[:200]}",
                    success=False,
                    base_url=settings.ntfy_base_url,
                    topic=settings.ntfy_topic,
                    timeout_s=settings.ntfy_timeout_s,
                )
                return JSONResponse(
                    status_code=503,
                    content={"queued": False, "reason": "full"},
                )
```

- [ ] **Step 4: Run test, see it pass**

```
uv run pytest tests/test_webhook_app.py -v
```

Expected: all PASS (new test + existing ones).

- [ ] **Step 5: Commit**

```bash
git add src/clayde/webhook/app.py tests/test_webhook_app.py
git commit -m "feat(pebble): ntfy on queue full before 503 response"
```

---

## Task 9: docker-compose KB mount + Dockerfile is already done — finalize docs

**Files:**
- Modify: `docker-compose.yml`
- Modify: `CLAUDE.md` (Pebble Webhook section + Configuration table)
- Modify: `README.md`

- [ ] **Step 1: Read docker-compose.yml to find the clayde volumes block**

```bash
cat docker-compose.yml
```

- [ ] **Step 2: Add KB mount under `clayde` → `volumes:`**

Insert below the `~/.claude/.credentials.json` mount (or wherever per-host mounts live):

```yaml
      - ~/knowledge_base:/home/clayde/knowledge_base
```

(No `:ro` — must be writable. Syncthing on the host handles sync.)

- [ ] **Step 3: Update CLAUDE.md Pebble Webhook section**

Open `CLAUDE.md`. Find the "Pebble Webhook" section. Replace it with:

```markdown
## Pebble Webhook

When `CLAYDE_PEBBLE_ENABLED=true`, the container also serves a FastAPI
webhook for a Pebble watch app, alongside the existing GitHub poll loop
(both run on the same asyncio event loop).

- `POST /webhook/pebble` — accepts `{"text": str, "timestamp": int}` with
  `Authorization: Bearer <CLAYDE_PEBBLE_TOKEN>`. Returns 200 with a job id.
- `GET /health` — liveness probe (no auth).

The text is dispatched to the Claude CLI with a system prompt listing
*skills* found under the in-container path `/skills/`. Each skill is a
single markdown file with frontmatter (`name`, `description`). Built-in
skills live at `/skills/builtin/` (baked into the image — currently
`ping`); host-mounted skill directories sit alongside (e.g.
`/skills/personal/`, `/skills/shared/`).

Claude is free to use any number of skills per request — there is no
single-skill cap. If no skill fits, Claude uses judgement (typically
capturing into the knowledge base inbox).

Per-request `cwd` is `${CLAYDE_KB_PATH}` (default
`/home/clayde/knowledge_base`), mounted RW from the host
`~/knowledge_base/`. Sync to other devices is handled by Syncthing on
the host — the container performs no `git` operations against the KB.

Every terminal outcome (success, claude-reported failure, timeout, usage
limit, CLI error, auth error, worker exception, queue full) emits an ntfy
notification on `${CLAYDE_NTFY_BASE_URL}/${CLAYDE_NTFY_TOPIC}`. Claude
produces the title/body via a fenced JSON tail in its output; framework
falls back to a synthetic "no summary" payload when parsing fails.

Traefik handles TLS (Let's Encrypt) and routes
`https://<CLAYDE_PEBBLE_HOST>/webhook/pebble` over a private docker
network. The `clayde` service is not attached to any externally-reachable
network — the only ingress path is through Traefik.
```

Also append these rows to the Configuration table:

```
| `CLAYDE_NTFY_TOPIC` | ntfy.sh topic for Pebble outcome notifications |
| `CLAYDE_NTFY_BASE_URL` | ntfy base URL (override for self-host) |
| `CLAYDE_NTFY_TIMEOUT_S` | ntfy POST timeout seconds (default 10) |
| `CLAYDE_KB_PATH` | In-container KB path; Pebble per-request cwd (default `/home/clayde/knowledge_base`) |
```

And update the `CLAYDE_PEBBLE_TIMEOUT` row's default note from `600` to `300`.

In the Project Structure block, add:

```
  webhook/
    ...existing files...
    notify.py           # send_ntfy + NotificationPayload model
  skills_builtin/
    ping.md             # built-in health-check skill (baked into image)
```

- [ ] **Step 4: Update README.md**

Mirror the same section/table updates in `README.md` (it duplicates much of CLAUDE.md's operator-facing content per phase 1).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml CLAUDE.md README.md
git commit -m "docs(pebble): document KB mount, multi-skill, ntfy outcome notifications"
```

---

## Task 10: End-to-end integration test

**Files:**
- Create: `tests/test_pebble_e2e.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_pebble_e2e.py`:

```python
"""End-to-end Pebble test: webhook → queue → worker → fake CLI → fake ntfy."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from clayde.webhook import worker as worker_mod
from clayde.webhook.app import create_app
from clayde.webhook.queue import JobQueue


@pytest.mark.asyncio
@respx.mock
async def test_e2e_pebble_voice_command_to_ntfy(monkeypatch, tmp_path):
    # Configure ntfy endpoint capture.
    ntfy_route = respx.post("https://ntfy.sh/test-topic").mock(
        return_value=httpx.Response(200, json={"id": "n1"})
    )

    # Patch settings used inside the worker's _notify helper.
    from clayde.config import _reset_settings
    _reset_settings()
    monkeypatch.setenv("CLAYDE_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("CLAYDE_NTFY_BASE_URL", "https://ntfy.sh")
    monkeypatch.setenv("CLAYDE_NTFY_TIMEOUT_S", "5")

    # Fake CLI: pretend Claude wrote a file and emitted JSON tail.
    async def fake_invoke(**kwargs):
        return (
            "I saved your note.\n\n"
            "```json\n"
            '{"title": "saved", "body": "wrote inbox/note.md", "success": true}\n'
            "```\n"
        )

    monkeypatch.setattr(worker_mod, "invoke_claude_pebble", fake_invoke)
    monkeypatch.setattr(worker_mod, "discover_skills", lambda root=None: [])
    monkeypatch.setattr(worker_mod, "build_system_prompt", lambda skills: "SYS")
    monkeypatch.setattr(worker_mod, "build_user_prompt", lambda text, ts: text)

    # Wire up the app + a real queue + a real worker_loop task.
    q = JobQueue(maxsize=4)
    app = create_app(queue=q, expected_token="tok")
    worker_task = asyncio.create_task(
        worker_mod.worker_loop(q, timeout_s=10, kb_path=str(tmp_path))
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/webhook/pebble",
                json={"text": "remember to buy milk", "timestamp": 1736000000},
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 200

        # Wait for the worker to process the job and POST to ntfy.
        for _ in range(50):
            if ntfy_route.called:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("ntfy POST never observed")
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    assert ntfy_route.called
    req = ntfy_route.calls.last.request
    assert req.headers["title"] == "saved"
    assert req.headers["priority"] == "3"
    assert req.headers["tags"] == "white_check_mark"
    assert req.content == b"wrote inbox/note.md"
```

- [ ] **Step 2: Run the test**

```
uv run pytest tests/test_pebble_e2e.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the full suite once more**

```
uv run pytest
```

Expected: full GREEN.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pebble_e2e.py
git commit -m "test(pebble): end-to-end webhook → worker → fake CLI → ntfy"
```

---

## Task 11: Open PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin clayde/pebble-robustness
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Pebble robustness: ntfy feedback, multi-skill, KB-default cwd" --body "$(cat <<'EOF'
## Summary

- Every Pebble webhook call now emits exactly one ntfy notification on
  the configured topic — success, claude-reported failure, timeout,
  usage-limit hit, CLI error, auth error, worker exception, or queue
  full. Auth/payload validation failures stay silent (abuse vector).
- Claude returns notification content via a fenced JSON tail
  (`title`, `body`, `success`). Framework falls back to a synthetic
  "no summary" payload when parsing fails — the call still notifies.
- Single-skill cap removed. The system prompt now invites Claude to
  use any number of skills, and to use judgement when no skill fits.
- `~/knowledge_base/` mounted RW at `/home/clayde/knowledge_base` and
  set as the per-request `cwd`. Syncthing on the host handles cross-
  device sync — the container performs no git operations.
- New built-in `ping` skill baked into the image for end-to-end chain
  verification from the watch.
- New `CliInvocationError` so the worker can distinguish CLI failure
  from a "successful" no-summary run.
- `CLAYDE_PEBBLE_TIMEOUT` default lowered 600 → 300 (pocket-dial guard).

Spec: `docs/superpowers/specs/2026-05-13-pebble-robustness-design.md`
Plan: `docs/superpowers/plans/2026-05-13-pebble-robustness.md`

## Test plan

- [ ] `uv run pytest` is fully green locally.
- [ ] On staging: speak "ping" into the Pebble app → receive `pong`
      notification within seconds.
- [ ] On staging: speak "remember to buy milk" → a file appears under
      `~/knowledge_base/inbox/` and a `saved` notification fires.
- [ ] On staging: force `CLAYDE_PEBBLE_QUEUE_MAX=0` and POST →
      observe `Pebble: queue full` notification + 503 response.
- [ ] On staging: stop ntfy.sh DNS (e.g. /etc/hosts) → the worker
      still records `outcome=success` in `traces.jsonl` with
      `notify.success=false` (best-effort invariant).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Verify PR shows green CI**

```bash
gh pr view --web
```

Watch the checks panel. If anything fails, address it as a follow-up commit on the same branch — do not amend the spec/plan commits.

---

## Self-review notes

- **Spec coverage:** every section of `2026-05-13-pebble-robustness-design.md` has at least one task — notification dispatch (T3, T7, T8), prompt + JSON contract (T4, T5), KB default cwd (T7), config (T1), built-in ping (T6), failure matrix (T7), tests (T7-T10), deployment (T6, T9), risks acknowledged inline (auth-error path in T7, JSON drift covered by parse fallback in T4).
- **Placeholder scan:** no "TBD" / "TODO" / "similar to". Every code step shows the actual code; every command shows the actual invocation.
- **Type consistency:** `process_job` and `worker_loop` accept `kb_path` consistently (T7 + orchestrator update). `NotificationPayload` and its field names match between T3, T4, T7 (`title`, `body`, `success`). `CliInvocationError.stderr` referenced consistently in T2 + T7. `send_ntfy` kwargs (`title`, `body`, `success`, `base_url`, `topic`, `timeout_s`) match between T3, T7, T8, T10.
