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
