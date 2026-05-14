"""Worker behavior: every terminal branch must emit exactly one send_ntfy call."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from clayde.claude import (
    CliInvocationError,
    InvocationTimeoutError,
    UsageLimitError,
)
from clayde.webhook import worker
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
