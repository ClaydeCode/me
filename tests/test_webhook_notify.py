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
async def test_send_ntfy_uses_failure_priority_and_tags_when_success_false():
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
