import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from clayde.claude import CliInvocationError, InvocationTimeoutError, UsageLimitError
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


async def test_runner_raises_usage_limit_on_is_error_output(fake_subproc, tmp_path):
    """When is_error=True and the limit pattern appears in result (not stderr),
    UsageLimitError must still be raised."""
    fake_subproc["proc"] = _FakeProc(
        stdout=json.dumps({"result": "you hit your usage limit", "is_error": True}).encode(),
        stderr=b"",
        returncode=1,
    )
    with pytest.raises(UsageLimitError):
        await runner.invoke_claude_pebble(
            system_prompt="s", user_text="t", cwd=str(tmp_path), timeout_s=10,
        )


async def test_runner_raises_runtime_error_on_auth_failure(fake_subproc, tmp_path):
    fake_subproc["proc"] = _FakeProc(
        stdout=b"{}",
        stderr=b"failed to authenticate",
        returncode=1,
    )
    with pytest.raises(RuntimeError, match="authentication failed"):
        await runner.invoke_claude_pebble(
            system_prompt="s", user_text="t", cwd=str(tmp_path), timeout_s=10,
        )


async def test_runner_kills_proc_on_external_cancel(fake_subproc, tmp_path):
    """If the runner coroutine is externally cancelled mid-communicate,
    the subprocess must be killed and reaped."""
    proc = _FakeProc(b"")

    started = asyncio.Event()

    async def hanging_communicate():
        started.set()
        await asyncio.sleep(60)

    proc.communicate = hanging_communicate
    fake_subproc["proc"] = proc

    task = asyncio.create_task(
        runner.invoke_claude_pebble(
            system_prompt="s", user_text="t", cwd=str(tmp_path), timeout_s=60,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    proc.kill.assert_called_once()


async def test_runner_raises_cli_invocation_error_on_nonzero(fake_subproc, tmp_path):
    fake_subproc["proc"] = _FakeProc(
        stdout=b'{"result": "boom"}', stderr=b"boom on stderr", returncode=2,
    )
    with pytest.raises(CliInvocationError) as exc:
        await runner.invoke_claude_pebble(
            system_prompt="sys", user_text="hi", cwd=str(tmp_path), timeout_s=5,
        )
    assert "boom" in exc.value.stderr


async def test_runner_returns_text_on_zero_exit(fake_subproc, tmp_path):
    fake_subproc["proc"] = _FakeProc(
        stdout=json.dumps({"result": "ok"}).encode(), stderr=b"", returncode=0,
    )
    out = await runner.invoke_claude_pebble(
        system_prompt="sys", user_text="hi", cwd=str(tmp_path), timeout_s=5,
    )
    assert out == "ok"
