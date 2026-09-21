# Scheduled Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a file-driven scheduler to Clayde that runs prompt tasks (one-off timestamp or recurring cron) from a host-mounted directory, independent of any interactive session, through the existing job pipeline.

**Architecture:** Lift the shared job-execution core out of `webhook/` into a neutral `service/` package. Add a `scheduler/` package whose `scheduler_loop()` coroutine joins the existing `asyncio.gather` in the orchestrator, scans `/tasks/*.md`, and enqueues due tasks as `Job(origin="scheduler")` into the shared `JobQueue`. The existing worker runs them via the Claude CLI; notification is by origin (scheduler = silent on success, framework ntfy on failure). Also mounts the whole KB skill library and switches the runner to auto permission mode.

**Tech Stack:** Python ≥3.12, `uv`, FastAPI/uvicorn, asyncio, pydantic-settings, `croniter`, stdlib `zoneinfo`, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-09-20-scheduled-tasks-design.md`

## Global Constraints

- Python ≥3.12, managed with `uv` (`~/.local/bin/uv`); run tests with `uv run pytest`.
- Commit style: Scoped Commits — `<scope>: <description>`, scope = subsystem (e.g. `service:`, `scheduler:`). No change-type prefixes like `fix(...)`.
- No AI-authorship footer or `Co-Authored-By` trailer on commits (ClaydeCode repo rule).
- All settings use the `CLAYDE_` env prefix, loaded by pydantic-settings from `data/config.env`.
- The whole suite must pass (`uv run pytest`) at the end of every task before committing.
- New dependency floor: `croniter>=2.0`.
- Behaviour-preserving moves must not change existing test assertions except import paths and the `PebbleJob`→`Job` / `invoke_claude_pebble`→`invoke_claude_job` renames.

---

### Task 1: Move execution core to `service/`, de-Pebble the names

Behaviour-preserving refactor. Moves the shared execution modules out of `webhook/`, renames `PebbleJob`→`Job` and `invoke_claude_pebble`→`invoke_claude_job`, relocates their tests. No logic changes.

**Files:**
- Move: `src/clayde/webhook/{queue,worker,runner,notify,skills}.py` → `src/clayde/service/`
- Create: `src/clayde/service/__init__.py`
- Modify: `src/clayde/webhook/__init__.py`, `src/clayde/orchestrator.py`, `src/clayde/webhook/app.py`
- Move tests: `tests/test_webhook_{queue,worker,runner,runner_parse,skills,notify}.py` → `tests/service/`; keep `tests/test_webhook_{app,auth}.py` → `tests/webhook/`
- Create: `tests/service/__init__.py`, `tests/webhook/__init__.py`

**Interfaces:**
- Produces: `clayde.service.queue.Job(id: str, text: str, timestamp: int)` (frozen dataclass; `origin` added in Task 2), `JobQueue`, `QueueFullError`; `clayde.service.worker.worker_loop`, `process_job`; `clayde.service.runner.invoke_claude_job`, `extract_notification_payload`; `clayde.service.notify.send_ntfy`, `NotificationPayload`; `clayde.service.skills.discover_skills`, `build_system_prompt`, `build_user_prompt`, `SKILLS_ROOT`, `Skill`.
- `clayde.service.__init__` re-exports `Job`, `JobQueue`, `QueueFullError`, `worker_loop`.

- [ ] **Step 1: Move the modules with git**

```bash
cd $(git rev-parse --show-toplevel)
mkdir -p src/clayde/service
git mv src/clayde/webhook/queue.py   src/clayde/service/queue.py
git mv src/clayde/webhook/worker.py  src/clayde/service/worker.py
git mv src/clayde/webhook/runner.py  src/clayde/service/runner.py
git mv src/clayde/webhook/notify.py  src/clayde/service/notify.py
git mv src/clayde/webhook/skills.py  src/clayde/service/skills.py
: > src/clayde/service/__init__.py
```

- [ ] **Step 2: Rename symbols and fix intra-package imports**

Global rename across `src/` and `tests/`:
- `PebbleJob` → `Job`
- `invoke_claude_pebble` → `invoke_claude_job`
- import paths `clayde.webhook.queue` → `clayde.service.queue` (and `worker`, `runner`, `notify`, `skills`).

```bash
grep -rl 'PebbleJob\|invoke_claude_pebble\|clayde\.webhook\.\(queue\|worker\|runner\|notify\|skills\)' src tests \
  | xargs sed -i \
    -e 's/PebbleJob/Job/g' \
    -e 's/invoke_claude_pebble/invoke_claude_job/g' \
    -e 's/clayde\.webhook\.queue/clayde.service.queue/g' \
    -e 's/clayde\.webhook\.worker/clayde.service.worker/g' \
    -e 's/clayde\.webhook\.runner/clayde.service.runner/g' \
    -e 's/clayde\.webhook\.notify/clayde.service.notify/g' \
    -e 's/clayde\.webhook\.skills/clayde.service.skills/g'
```

- [ ] **Step 3: Populate `service/__init__.py`**

```python
from clayde.service.queue import Job, JobQueue, QueueFullError
from clayde.service.worker import worker_loop

__all__ = ["Job", "JobQueue", "QueueFullError", "worker_loop"]
```

- [ ] **Step 4: Update `webhook/__init__.py` and orchestrator imports**

`webhook/__init__.py` currently re-exports `JobQueue, create_app, worker_loop`. Make it:

```python
from clayde.service import Job, JobQueue, QueueFullError, worker_loop
from clayde.webhook.app import create_app

__all__ = ["Job", "JobQueue", "QueueFullError", "worker_loop", "create_app"]
```

`orchestrator.py` keeps `from clayde.webhook import JobQueue, create_app, worker_loop` — still valid via the re-export. Leave it.

- [ ] **Step 5: Relocate tests into packages**

```bash
mkdir -p tests/service tests/webhook
: > tests/service/__init__.py
: > tests/webhook/__init__.py
git mv tests/test_webhook_queue.py        tests/service/test_queue.py
git mv tests/test_webhook_worker.py       tests/service/test_worker.py
git mv tests/test_webhook_runner.py       tests/service/test_runner.py
git mv tests/test_webhook_runner_parse.py tests/service/test_runner_parse.py
git mv tests/test_webhook_skills.py       tests/service/test_skills.py
git mv tests/test_webhook_notify.py       tests/service/test_notify.py
git mv tests/test_webhook_app.py          tests/webhook/test_app.py
git mv tests/test_webhook_auth.py         tests/webhook/test_auth.py
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, 364 tests, 0 failures (same count as baseline — only paths/names changed).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "service: move job-execution core out of webhook, rename PebbleJob to Job"
```

---

### Task 2: Add `Job.origin` and generalise the process span

**Files:**
- Modify: `src/clayde/service/queue.py` (add field)
- Modify: `src/clayde/service/worker.py` (span name + attribute)
- Test: `tests/service/test_queue.py`, `tests/service/test_worker.py`

**Interfaces:**
- Produces: `Job(id, text, timestamp, origin="pebble")` where `origin ∈ {"pebble","scheduler"}`.

- [ ] **Step 1: Write failing test for the default and field**

Add to `tests/service/test_queue.py`:

```python
from clayde.service.queue import Job

def test_job_origin_defaults_to_pebble():
    job = Job(id="1", text="hi", timestamp=0)
    assert job.origin == "pebble"

def test_job_origin_can_be_scheduler():
    job = Job(id="1", text="hi", timestamp=0, origin="scheduler")
    assert job.origin == "scheduler"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/service/test_queue.py -q`
Expected: FAIL (`Job() got an unexpected keyword argument 'origin'`).

- [ ] **Step 3: Add the field**

In `src/clayde/service/queue.py`:

```python
@dataclass(frozen=True)
class Job:
    id: str
    text: str
    timestamp: int
    origin: str = "pebble"
```

- [ ] **Step 4: Generalise the worker span**

In `src/clayde/service/worker.py`, `process_job`, rename the span and add the attribute:

```python
with tracer.start_as_current_span("clayde.job.process") as span:
    span.set_attribute("job.origin", job.origin)
    span.set_attribute("pebble.job_id", job.id)
    # ... existing attributes unchanged
```

Update any assertion in `tests/service/test_worker.py` referencing `clayde.pebble.process` to `clayde.job.process`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/service -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "service: add Job.origin and rename process span to clayde.job.process"
```

---

### Task 3: Switch the runner to auto permission mode

**Files:**
- Modify: `src/clayde/service/runner.py` (`invoke_claude_job` argv)
- Test: `tests/service/test_runner.py`

- [ ] **Step 1: Write failing test asserting the argv**

The runner builds `cmd` before `create_subprocess_exec`. Add a test that inspects the command by monkeypatching `asyncio.create_subprocess_exec` to capture args. Add to `tests/service/test_runner.py`:

```python
import asyncio
import clayde.service.runner as runner

async def test_invoke_uses_auto_permission_mode(monkeypatch):
    captured = {}

    class FakeProc:
        returncode = 0
        async def communicate(self):
            return (b'{"result": "ok", "is_error": false}', b"")
        def kill(self): pass
        async def wait(self): return 0

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await runner.invoke_claude_job(
        system_prompt="s", user_text="u", cwd="/tmp", timeout_s=5,
    )
    args = captured["args"]
    assert "--permission-mode" in args
    assert "auto" in args
    assert "--permission-prompts" in args
    assert "none" in args
    assert "--dangerously-skip-permissions" not in args
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/service/test_runner.py::test_invoke_uses_auto_permission_mode -q`
Expected: FAIL (`--dangerously-skip-permissions` still present).

- [ ] **Step 3: Change the argv**

In `src/clayde/service/runner.py`, replace the `--dangerously-skip-permissions` element:

```python
cmd = [
    cli_bin,
    "-p", user_text,
    "--append-system-prompt", system_prompt,
    "--output-format", "json",
    "--permission-mode", "auto",
    "--permission-prompts", "none",
]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/service -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "service: run the CLI under auto permission mode instead of skip-permissions"
```

---

### Task 4: Notification by origin (scheduler success is silent)

**Files:**
- Modify: `src/clayde/service/worker.py` (`process_job` success branch)
- Test: `tests/service/test_worker.py`

**Interfaces:**
- Consumes: `Job.origin` (Task 2).

- [ ] **Step 1: Write failing tests**

Add to `tests/service/test_worker.py` (follow the file's existing style for stubbing `invoke_claude_job` and `send_ntfy`; assert on whether `_notify`/`send_ntfy` was called):

```python
async def test_scheduler_success_does_not_notify(monkeypatch):
    calls = _stub_success_run(monkeypatch)  # existing helper pattern; returns notify-call recorder
    job = Job(id="1", text="t", timestamp=0, origin="scheduler")
    await process_job(job, timeout_s=5, kb_path="/tmp")
    assert calls.notify_count == 0

async def test_scheduler_failure_notifies(monkeypatch):
    calls = _stub_timeout_run(monkeypatch)
    job = Job(id="1", text="t", timestamp=0, origin="scheduler")
    await process_job(job, timeout_s=5, kb_path="/tmp")
    assert calls.notify_count == 1

async def test_pebble_success_still_notifies(monkeypatch):
    calls = _stub_success_run(monkeypatch)
    job = Job(id="1", text="t", timestamp=0, origin="pebble")
    await process_job(job, timeout_s=5, kb_path="/tmp")
    assert calls.notify_count == 1
```

If the test file has no such helpers, write the two stubs inline using `monkeypatch.setattr` on `clayde.service.worker.invoke_claude_job` (return a JSON-tail string for success; raise `InvocationTimeoutError` for timeout) and on `clayde.service.worker.send_ntfy` (record calls).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/service/test_worker.py -k scheduler -q`
Expected: FAIL (scheduler success currently notifies).

- [ ] **Step 3: Gate the success notify on origin**

In `process_job`, the success path currently calls `await _notify(...)` after parsing the payload. Wrap only that success call:

```python
if job.origin != "scheduler":
    await _notify(title=payload.title, body=payload.body, success=payload.success)
log.info("[%s] processed outcome=%s", job.id, outcome)
```

Leave every failure/except branch's `_notify` untouched.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/service/test_worker.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "service: suppress success notification for scheduler-origin jobs"
```

---

### Task 5: Origin-aware prompt framing

**Files:**
- Modify: `src/clayde/service/skills.py` (`build_system_prompt`, `build_user_prompt`)
- Modify: `src/clayde/service/worker.py` (pass `job.origin` into the builders)
- Test: `tests/service/test_skills.py`, `tests/service/test_worker.py`

**Interfaces:**
- Produces: `build_system_prompt(skills, timeout_s=300, origin="pebble")`, `build_user_prompt(text, timestamp, origin="pebble")`.

- [ ] **Step 1: Write failing tests**

Add to `tests/service/test_skills.py`:

```python
from clayde.service.skills import build_system_prompt, build_user_prompt

def test_system_prompt_scheduler_framing():
    p = build_system_prompt([], timeout_s=300, origin="scheduler")
    assert "scheduled task" in p.lower()
    assert "pebble watch" not in p.lower()

def test_system_prompt_pebble_framing_unchanged():
    p = build_system_prompt([], timeout_s=300, origin="pebble")
    assert "pebble watch" in p.lower()

def test_user_prompt_scheduler_has_no_timestamp_prefix():
    assert build_user_prompt("do it", 123, origin="scheduler") == "do it"

def test_user_prompt_pebble_unchanged():
    assert build_user_prompt("do it", 123, origin="pebble") == "(timestamp 123)\ndo it"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/service/test_skills.py -k framing -q`
Expected: FAIL (`origin` kwarg unknown).

- [ ] **Step 3: Parametrise the builders**

In `src/clayde/service/skills.py`, split the opening line by origin. Replace the hardcoded first line of `_SYSTEM_PROMPT_TEMPLATE` with a `{intro}` placeholder and choose it in `build_system_prompt`:

```python
_INTRO = {
    "pebble": "You are Clayde, executing a request from the user via a Pebble watch.",
    "scheduler": "You are Clayde, executing a scheduled task.",
}

def build_system_prompt(skills, timeout_s: int = 300, origin: str = "pebble") -> str:
    ...
    return _SYSTEM_PROMPT_TEMPLATE.format(
        intro=_INTRO.get(origin, _INTRO["pebble"]),
        skill_section=skill_section, timeout_s=timeout_s,
    )

def build_user_prompt(text: str, timestamp: int, origin: str = "pebble") -> str:
    if origin == "scheduler":
        return text
    return f"(timestamp {timestamp})\n{text}"
```

(Adjust `_SYSTEM_PROMPT_TEMPLATE` so it begins with `{intro}\n` in place of the current literal first sentence.)

- [ ] **Step 4: Thread origin through the worker**

In `src/clayde/service/worker.py`, `process_job`:

```python
system_prompt = build_system_prompt(skills, timeout_s=timeout_s, origin=job.origin)
user_text = build_user_prompt(job.text, job.timestamp, origin=job.origin)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/service -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "service: origin-aware system and user prompt framing"
```

---

### Task 6: `SKILL.md`-aware skill discovery

**Files:**
- Modify: `src/clayde/service/skills.py` (`discover_skills`)
- Test: `tests/service/test_skills.py`

**Interfaces:**
- Unchanged public signature `discover_skills(root=SKILLS_ROOT) -> list[Skill]`.

- [ ] **Step 1: Write failing tests**

Add to `tests/service/test_skills.py` (uses `tmp_path`):

```python
from clayde.service.skills import discover_skills

def _write(p, name, desc):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody\n")

def test_directory_skill_matched(tmp_path):
    _write(tmp_path / "kb" / "ntfy-ping" / "SKILL.md", "ntfy-ping", "send a push")
    names = {s.name for s in discover_skills(tmp_path)}
    assert "ntfy-ping" in names

def test_reference_md_ignored_without_warning(tmp_path, caplog):
    _write(tmp_path / "kb" / "foo" / "SKILL.md", "foo", "the foo skill")
    (tmp_path / "kb" / "foo" / "references").mkdir(parents=True)
    (tmp_path / "kb" / "foo" / "references" / "notes.md").write_text("# just notes\n")
    names = {s.name for s in discover_skills(tmp_path)}
    assert names == {"foo"}
    assert "Failed to parse skill" not in caplog.text

def test_flat_builtin_md_matched(tmp_path):
    _write(tmp_path / "builtin" / "ping.md", "ping", "health check")
    names = {s.name for s in discover_skills(tmp_path)}
    assert "ping" in names
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/service/test_skills.py -k "matched or ignored" -q`
Expected: FAIL (reference file logged/parsed; or builtin flat file handling differs).

- [ ] **Step 3: Filter candidates before parsing**

In `discover_skills`, replace the `all_files = sorted(root.rglob("*.md"))` line with a candidate filter:

```python
def _is_skill_candidate(p: Path) -> bool:
    # Directory skills use SKILL.md; the flat builtin format lives under builtin/.
    return p.name == "SKILL.md" or p.parent.name == "builtin"

all_files = sorted(p for p in root.rglob("*.md") if _is_skill_candidate(p))
```

Keep the existing non-builtin-first ordering and name de-duplication. `_is_builtin` and the rest are unchanged.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/service/test_skills.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "service: discover SKILL.md directory skills, ignore reference markdown"
```

---

### Task 7: Dependency + scheduler settings

**Files:**
- Modify: `pyproject.toml` (add `croniter`)
- Modify: `src/clayde/config.py` (new settings)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces on `Settings`: `scheduler_enabled: bool`, `scheduler_dir: str`, `scheduler_interval_s: int`, `scheduler_tz: str`, `scheduler_timeout: int`.

- [ ] **Step 1: Add croniter and sync**

In `pyproject.toml`, add to `[project.dependencies]`: `"croniter>=2.0"`. Then:

```bash
uv sync --extra dev
```

- [ ] **Step 2: Write failing test for defaults**

Add to `tests/test_config.py`:

```python
def test_scheduler_settings_defaults(monkeypatch):
    from clayde.config import _reset_settings, get_settings
    _reset_settings()
    s = get_settings()
    assert s.scheduler_enabled is False
    assert s.scheduler_dir == "/tasks"
    assert s.scheduler_interval_s == 30
    assert s.scheduler_tz == "Europe/Berlin"
    assert s.scheduler_timeout == 300
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_config.py::test_scheduler_settings_defaults -q`
Expected: FAIL (attributes missing).

- [ ] **Step 4: Add the settings**

In `src/clayde/config.py`, in `Settings`, after the Pebble block:

```python
    # Scheduler
    scheduler_enabled: bool = False
    scheduler_dir: str = "/tasks"
    scheduler_interval_s: int = 30
    scheduler_tz: str = "Europe/Berlin"
    scheduler_timeout: int = 300
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "scheduler: add croniter dependency and scheduler settings"
```

---

### Task 8: Task file parsing (`scheduler/tasks.py`)

**Files:**
- Create: `src/clayde/scheduler/__init__.py`, `src/clayde/scheduler/tasks.py`
- Test: `tests/scheduler/__init__.py`, `tests/scheduler/test_tasks.py`

**Interfaces:**
- Produces: `ScheduledTask(path: Path, prompt: str, cron: str | None, at: datetime | None, tz: ZoneInfo, enabled: bool, title: str | None)`; `parse_task_file(path: Path, default_tz: str) -> ScheduledTask` (raises `ValueError` on malformed); `discover_tasks(root: Path, default_tz: str) -> list[ScheduledTask]` (skips `done/`, logs+skips malformed).

- [ ] **Step 1: Write failing tests**

Create `tests/scheduler/test_tasks.py`:

```python
from datetime import datetime
from pathlib import Path
import pytest
from clayde.scheduler.tasks import parse_task_file, discover_tasks, ScheduledTask

def _w(p: Path, fm: str, body: str = "do the thing"):
    p.write_text(f"---\n{fm}\n---\n{body}\n")

def test_parse_cron(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"')
    t = parse_task_file(f, "Europe/Berlin")
    assert t.cron == "0 8 * * *" and t.at is None and t.enabled is True
    assert t.prompt.strip() == "do the thing"

def test_parse_at(tmp_path):
    f = tmp_path / "k.md"; _w(f, "at: 2026-09-21T08:00")
    t = parse_task_file(f, "Europe/Berlin")
    assert t.at == datetime(2026, 9, 21, 8, 0, tzinfo=t.tz) and t.cron is None

def test_both_keys_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\nat: 2026-09-21T08:00')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin")

def test_neither_key_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, "title: x")
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin")

def test_bad_cron_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "not a cron"')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin")

def test_bad_tz_rejected(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\ntz: Mars/Phobos')
    with pytest.raises(ValueError):
        parse_task_file(f, "Europe/Berlin")

def test_enabled_false(tmp_path):
    f = tmp_path / "k.md"; _w(f, 'cron: "0 8 * * *"\nenabled: false')
    assert parse_task_file(f, "Europe/Berlin").enabled is False

def test_discover_skips_done_and_malformed(tmp_path, caplog):
    _w(tmp_path / "good.md", 'cron: "0 8 * * *"')
    (tmp_path / "bad.md").write_text("no frontmatter")
    (tmp_path / "done").mkdir()
    _w(tmp_path / "done" / "old.md", 'cron: "0 8 * * *"')
    tasks = discover_tasks(tmp_path, "Europe/Berlin")
    assert [t.path.name for t in tasks] == ["good.md"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_tasks.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `tasks.py`**

```python
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
```

Note: `root.glob("*.md")` is non-recursive, so `done/` is skipped automatically.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/scheduler/test_tasks.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "scheduler: task-file model, parsing, and discovery"
```

---

### Task 9: Scheduler state (`scheduler/state.py`)

**Files:**
- Create: `src/clayde/scheduler/state.py`
- Test: `tests/scheduler/test_state.py`

**Interfaces:**
- Produces: `load_state(path: Path) -> dict`; `save_state(path: Path, state: dict) -> None`; `get_last_fired(state: dict, key: str) -> datetime | None`; `set_last_fired(state: dict, key: str, dt: datetime) -> None`.

- [ ] **Step 1: Write failing tests**

Create `tests/scheduler/test_state.py`:

```python
from datetime import datetime, timezone
from clayde.scheduler.state import load_state, save_state, get_last_fired, set_last_fired

def test_missing_file_is_empty(tmp_path):
    assert load_state(tmp_path / "none.json") == {"recurring": {}}

def test_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    state = load_state(p)
    dt = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    set_last_fired(state, "keep-warm.md", dt)
    save_state(p, state)
    again = load_state(p)
    assert get_last_fired(again, "keep-warm.md") == dt

def test_get_missing_key_is_none(tmp_path):
    assert get_last_fired(load_state(tmp_path / "s.json"), "x") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_state.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `state.py`**

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/scheduler/test_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "scheduler: recurring-task run-state persistence"
```

---

### Task 10: Due-ness and lateness (pure functions in `scheduler/loop.py`)

**Files:**
- Create: `src/clayde/scheduler/loop.py` (pure helpers first; the async loop is Task 11)
- Test: `tests/scheduler/test_schedule.py`

**Interfaces:**
- Produces: `baseline_recurring(cron: str, tz, now: datetime) -> datetime`; `recurring_due(cron: str, tz, now: datetime, last_fired: datetime) -> datetime | None`; `oneoff_due(at: datetime, now: datetime) -> bool`; `lateness_note(scheduled: datetime, now: datetime) -> str | None`.

- [ ] **Step 1: Write failing tests**

Create `tests/scheduler/test_schedule.py`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from clayde.scheduler.loop import (
    baseline_recurring, recurring_due, oneoff_due, lateness_note,
)

TZ = ZoneInfo("Europe/Berlin")

def _at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)

def test_baseline_is_last_past_occurrence(tmp=None):
    now = _at(2026, 9, 21, 15, 0)
    assert baseline_recurring("0 8 * * *", TZ, now) == _at(2026, 9, 21, 8, 0)

def test_recurring_fires_once_after_occurrence():
    now = _at(2026, 9, 21, 8, 0)
    last = _at(2026, 9, 20, 8, 0)
    assert recurring_due("0 8 * * *", TZ, now, last) == _at(2026, 9, 21, 8, 0)

def test_recurring_not_due_when_already_fired():
    now = _at(2026, 9, 21, 8, 30)
    last = _at(2026, 9, 21, 8, 0)
    assert recurring_due("0 8 * * *", TZ, now, last) is None

def test_recurring_single_fire_after_downtime():
    # down for two days; only the most recent occurrence fires, once
    now = _at(2026, 9, 23, 9, 0)
    last = _at(2026, 9, 20, 8, 0)
    assert recurring_due("0 8 * * *", TZ, now, last) == _at(2026, 9, 23, 8, 0)

def test_oneoff_due():
    assert oneoff_due(_at(2026, 9, 21, 8, 0), _at(2026, 9, 21, 8, 1)) is True
    assert oneoff_due(_at(2026, 9, 21, 8, 0), _at(2026, 9, 21, 7, 59)) is False

def test_lateness_note_present_when_late():
    note = lateness_note(_at(2026, 9, 21, 8, 0), _at(2026, 9, 21, 9, 0))
    assert note is not None and "late" in note.lower()

def test_lateness_note_absent_when_on_time():
    assert lateness_note(_at(2026, 9, 21, 8, 0), _at(2026, 9, 21, 8, 0, 30)) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_schedule.py -q`
Expected: FAIL (module/functions missing).

- [ ] **Step 3: Implement the pure helpers**

Create `src/clayde/scheduler/loop.py` with (async loop added in Task 11):

```python
"""Scheduler tick loop and its pure scheduling helpers."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from croniter import croniter

log = logging.getLogger("clayde.scheduler")

_LATE_THRESHOLD = timedelta(seconds=60)


def baseline_recurring(cron: str, tz, now: datetime) -> datetime:
    return croniter(cron, now.astimezone(tz)).get_prev(datetime)


def recurring_due(cron: str, tz, now: datetime, last_fired: datetime) -> datetime | None:
    prev = croniter(cron, now.astimezone(tz)).get_prev(datetime)
    return prev if prev > last_fired else None


def oneoff_due(at: datetime, now: datetime) -> bool:
    return now >= at


def lateness_note(scheduled: datetime, now: datetime) -> str | None:
    delta = now - scheduled
    if delta <= _LATE_THRESHOLD:
        return None
    mins = int(delta.total_seconds() // 60)
    when = scheduled.strftime("%Y-%m-%d %H:%M %Z")
    dur = f"{mins} min" if mins else f"{int(delta.total_seconds())} s"
    return f"[This task was scheduled for {when} and is running {dur} late.]"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/scheduler/test_schedule.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "scheduler: pure due-ness and lateness helpers"
```

---

### Task 11: Scheduler loop wiring (`scheduler/loop.py`)

**Files:**
- Modify: `src/clayde/scheduler/loop.py` (add `scheduler_loop` + one-off move)
- Test: `tests/scheduler/test_loop.py`

**Interfaces:**
- Consumes: `JobQueue`/`Job`/`QueueFullError` (service), `discover_tasks` (Task 8), state helpers (Task 9), due-ness helpers (Task 10).
- Produces: `async scheduler_loop(queue, *, tasks_dir: Path, state_path: Path, default_tz: str, interval_s: int) -> None`; `run_tick(queue, *, tasks_dir: Path, state_path: Path, default_tz: str, now: datetime) -> None` (single tick, the testable unit).

- [ ] **Step 1: Write failing tests**

Create `tests/scheduler/test_loop.py`:

```python
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from clayde.service.queue import JobQueue
from clayde.scheduler.loop import run_tick

TZ = "Europe/Berlin"

def _w(d: Path, name: str, fm: str, body="do it"):
    (d / name).write_text(f"---\n{fm}\n---\n{body}\n")

async def _drain(q: JobQueue):
    out = []
    while not q._q.empty():
        out.append(await q.get())
    return out

async def test_first_encounter_baselines_without_firing(tmp_path):
    _w(tmp_path, "k.md", 'cron: "0 8 * * *"')
    q = JobQueue(maxsize=10)
    now = datetime(2026, 9, 21, 15, 0, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=tmp_path / "s.json", default_tz=TZ, now=now)
    assert await _drain(q) == []
    assert (tmp_path / "s.json").exists()

async def test_recurring_fires_and_dedups(tmp_path):
    _w(tmp_path, "k.md", 'cron: "0 8 * * *"')
    q = JobQueue(maxsize=10)
    sp = tmp_path / "s.json"
    # seed state so it's not first-encounter
    from clayde.scheduler.state import load_state, save_state, set_last_fired
    st = load_state(sp); set_last_fired(st, "k.md", datetime(2026, 9, 20, 8, 0, tzinfo=ZoneInfo(TZ))); save_state(sp, st)
    now = datetime(2026, 9, 21, 8, 0, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=sp, default_tz=TZ, now=now)
    jobs = await _drain(q)
    assert len(jobs) == 1 and jobs[0].origin == "scheduler"
    # second tick same minute: no duplicate
    run_tick(q, tasks_dir=tmp_path, state_path=sp, default_tz=TZ, now=now)
    assert await _drain(q) == []

async def test_oneoff_fires_and_moves_to_done(tmp_path):
    _w(tmp_path, "call.md", "at: 2026-09-21T08:00")
    q = JobQueue(maxsize=10)
    now = datetime(2026, 9, 21, 8, 1, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=tmp_path / "s.json", default_tz=TZ, now=now)
    jobs = await _drain(q)
    assert len(jobs) == 1
    assert not (tmp_path / "call.md").exists()
    assert list((tmp_path / "done").glob("*call.md"))

async def test_late_oneoff_prepends_note(tmp_path):
    _w(tmp_path, "call.md", "at: 2026-09-21T08:00", body="ring the bell")
    q = JobQueue(maxsize=10)
    now = datetime(2026, 9, 21, 9, 0, tzinfo=ZoneInfo(TZ))
    run_tick(q, tasks_dir=tmp_path, state_path=tmp_path / "s.json", default_tz=TZ, now=now)
    jobs = await _drain(q)
    assert "late" in jobs[0].text.lower() and "ring the bell" in jobs[0].text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_loop.py -q`
Expected: FAIL (`run_tick` missing).

- [ ] **Step 3: Implement `run_tick`, `scheduler_loop`, and the move helper**

Append to `src/clayde/scheduler/loop.py`:

```python
import asyncio
import shutil
import uuid
from pathlib import Path

from clayde.service.queue import Job, JobQueue, QueueFullError
from clayde.scheduler.state import (
    load_state, save_state, get_last_fired, set_last_fired,
)
from clayde.scheduler.tasks import discover_tasks


def _move_to_done(path: Path, now: datetime) -> None:
    done = path.parent / "done"
    done.mkdir(exist_ok=True)
    shutil.move(str(path), str(done / f"{int(now.timestamp())}-{path.name}"))


def run_tick(queue: JobQueue, *, tasks_dir: Path, state_path: Path,
             default_tz: str, now: datetime) -> None:
    state = load_state(state_path)
    for task in discover_tasks(tasks_dir, default_tz):
        if not task.enabled:
            continue
        key = task.path.name
        scheduled: datetime | None = None

        if task.cron is not None:
            last = get_last_fired(state, key)
            if last is None:
                set_last_fired(state, key, baseline_recurring(task.cron, task.tz, now))
                continue
            scheduled = recurring_due(task.cron, task.tz, now, last)
        elif oneoff_due(task.at, now):
            scheduled = task.at

        if scheduled is None:
            continue

        note = lateness_note(scheduled, now)
        text = f"{note}\n{task.prompt}" if note else task.prompt
        job = Job(id=str(uuid.uuid4()), text=text,
                  timestamp=int(now.timestamp()), origin="scheduler")
        try:
            queue.enqueue(job)
        except QueueFullError:
            log.warning("Queue full — deferring task %s", key)
            continue
        if task.cron is not None:
            set_last_fired(state, key, scheduled)
        else:
            _move_to_done(task.path, now)

    save_state(state_path, state)


async def scheduler_loop(queue: JobQueue, *, tasks_dir: str, state_path: str,
                         default_tz: str, interval_s: int) -> None:
    log.info("Scheduler loop started (dir=%s, interval=%ds)", tasks_dir, interval_s)
    from datetime import timezone
    while True:
        try:
            run_tick(queue, tasks_dir=Path(tasks_dir), state_path=Path(state_path),
                     default_tz=default_tz, now=datetime.now(timezone.utc))
        except Exception:
            log.exception("Scheduler tick failed — continuing")
        await asyncio.sleep(interval_s)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/scheduler -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "scheduler: tick loop — enqueue due tasks, dedup, move one-offs"
```

---

### Task 12: Wire the scheduler into the orchestrator

**Files:**
- Modify: `src/clayde/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `scheduler_loop` (Task 11), scheduler settings (Task 7).

- [ ] **Step 1: Write failing test**

The orchestrator builds a task list in `_run_with_pebble`. Extract the decision into a testable helper `_scheduler_enabled(settings) -> bool` (returns `settings.scheduler_enabled`) and assert wiring via the state path. Add to `tests/test_orchestrator.py`:

```python
def test_scheduler_state_path_under_data():
    from clayde.orchestrator import _scheduler_state_path
    assert _scheduler_state_path().endswith("/scheduler_state.json")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_orchestrator.py::test_scheduler_state_path_under_data -q`
Expected: FAIL (helper missing).

- [ ] **Step 3: Add the helper and gather the loop**

In `src/clayde/orchestrator.py`:

```python
from clayde.config import DATA_DIR
from clayde.scheduler.loop import scheduler_loop

def _scheduler_state_path() -> str:
    return str(DATA_DIR / "scheduler_state.json")
```

In `_run_with_pebble`, after the `worker_task` definition and before `tasks = [...]`:

```python
    async def scheduler_task() -> None:
        await scheduler_loop(
            queue,
            tasks_dir=settings.scheduler_dir,
            state_path=_scheduler_state_path(),
            default_tz=settings.scheduler_tz,
            interval_s=settings.scheduler_interval_s,
        )

    tasks = [server.serve(), worker_task()]
    if settings.scheduler_enabled:
        log.info("Scheduler loop enabled")
        tasks.append(scheduler_task())
    else:
        log.info("Scheduler loop disabled (CLAYDE_SCHEDULER_ENABLED not set)")
    if settings.fs_enabled:
        ...  # existing freeshard block unchanged
```

Note: scheduler jobs use `settings.scheduler_timeout`; the worker currently takes a single `timeout_s`. For v1 the shared worker uses `pebble_timeout` for all jobs. Deferring per-origin timeout keeps the worker unchanged; record it as a follow-up in the commit body.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: PASS (full suite).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "orchestrator: run the scheduler loop alongside the webhook when enabled"
```

---

### Task 13: Deployment config and docs

**Files:**
- Modify: `docker-compose.yml`, `config.env.template`, `README.md`, `CLAUDE.md`
- No tests (config/docs).

- [ ] **Step 1: Add mounts to docker-compose**

In the `clayde` service `volumes:`, add:

```yaml
      - ~/clayde-tasks:/tasks
      - ~/knowledge_base/skills:/skills/kb:ro
```

- [ ] **Step 2: Document the new settings**

In `config.env.template`, add commented entries for `CLAYDE_SCHEDULER_ENABLED`, `CLAYDE_SCHEDULER_DIR`, `CLAYDE_SCHEDULER_INTERVAL_S`, `CLAYDE_SCHEDULER_TZ`, `CLAYDE_SCHEDULER_TIMEOUT` with their defaults.

- [ ] **Step 3: README + CLAUDE.md**

Add a "Scheduler" section to `README.md`: the `~/clayde-tasks/` format (cron/at frontmatter, body = prompt), the `done/` behaviour, notification model (silent success, framework failure ntfy, prompt-driven notify via the `ntfy-ping` skill), the whole-library skill mount, the auto permission mode, and the **bootstrapping caveat** (the CLI login must be established once and not left to lapse before the first keep-warm tick). Add a short pointer in `CLAUDE.md` under Configuration and Project Structure (new `scheduler/` package, `service/` rename).

- [ ] **Step 4: Sanity-check compose**

Run: `docker compose -f docker-compose.yml config >/dev/null && echo OK`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "scheduler: deployment mounts, config template, and docs"
```

---

## Self-Review

**Spec coverage:**
- service/ move + Job rename → Task 1; Job.origin + span → Task 2; auto permission mode → Task 3; notify-by-origin → Task 4; origin prompt framing → Task 5; SKILL.md discovery → Task 6; croniter + settings → Task 7; task parsing → Task 8; state → Task 9; due-ness/lateness → Task 10; loop (enqueue/dedup/move) → Task 11; orchestrator wiring → Task 12; compose mounts + docs + bootstrapping caveat → Task 13. All spec sections mapped.
- Deferred by spec (non-goals): crash-safe delivery, backfill, per-task silence-on-failure — none implemented, as intended. Per-origin timeout (`scheduler_timeout`) is defined in settings but the worker still uses one timeout in v1; noted in Task 12 as a follow-up rather than silently dropped.

**Placeholder scan:** No TBD/TODO; every code step carries real code; test steps carry real assertions.

**Type consistency:** `Job(id, text, timestamp, origin="pebble")` consistent across Tasks 1–12. `invoke_claude_job` consistent (Tasks 1, 3). `build_system_prompt(..., origin=)` / `build_user_prompt(..., origin=)` consistent (Tasks 5, and worker call). `ScheduledTask` fields consistent between Task 8 (producer) and Tasks 10–11 (consumers: `.cron`, `.at`, `.tz`, `.prompt`, `.enabled`, `.path`). State helpers `get_last_fired`/`set_last_fired` consistent between Task 9 and Task 11. Due-ness helper names consistent between Task 10 and Task 11.
