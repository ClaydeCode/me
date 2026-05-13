"""End-to-end Pebble test: webhook → queue → worker → fake CLI → fake ntfy."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from clayde.config import _reset_settings
from clayde.webhook import worker as worker_mod
from clayde.webhook.app import create_app
from clayde.webhook.queue import JobQueue


@pytest.mark.asyncio
@respx.mock
async def test_e2e_pebble_voice_command_to_ntfy(monkeypatch, tmp_path):
    # Mock ntfy endpoint.
    ntfy_route = respx.post("https://ntfy.sh/test-topic").mock(
        return_value=httpx.Response(200, json={"id": "n1"})
    )

    # Settings override — reset singleton so env wins.
    _reset_settings()
    monkeypatch.setenv("CLAYDE_NTFY_TOPIC", "test-topic")
    monkeypatch.setenv("CLAYDE_NTFY_BASE_URL", "https://ntfy.sh")
    monkeypatch.setenv("CLAYDE_NTFY_TIMEOUT_S", "5")

    # Fake the CLI invocation: return text with a JSON tail.
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

    # Real queue + real worker_loop.
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

        # Wait for the worker to drain the queue and post to ntfy.
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
        _reset_settings()

    assert ntfy_route.called
    req = ntfy_route.calls.last.request
    assert req.headers["title"] == "saved"
    assert req.headers["priority"] == "3"
    assert req.headers["tags"] == "white_check_mark"
    assert req.content == b"wrote inbox/note.md"
