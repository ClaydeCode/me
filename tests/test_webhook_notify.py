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


def test_notification_payload_em_dash_in_title_normalised():
    # Real prod failure: em dash in title raised UnicodeEncodeError when
    # httpx serialised the header as latin-1.
    p = NotificationPayload(title="Thomas Stegger — plant prefs saved", body="ok", success=True)
    assert "—" not in p.title
    assert p.title == "Thomas Stegger - plant prefs saved"
    # Must round-trip cleanly through latin-1 (the header codec httpx uses).
    p.title.encode("latin-1")


def test_notification_payload_smart_quotes_in_title_normalised():
    p = NotificationPayload(title="“hi” ‘there’", body="ok", success=True)
    assert p.title == '"hi" \'there\''


def test_notification_payload_unknown_unicode_in_title_replaced():
    p = NotificationPayload(title="emoji \U0001f600 tail", body="ok", success=True)
    assert "\U0001f600" not in p.title
    p.title.encode("ascii")


def test_notification_payload_ascii_coercion_runs_before_clamp():
    # "..." (3 chars) replaces "…" (1 char); clamp comes after, so a
    # title that fit pre-replacement may not fit after — and that's fine.
    long = "a" * 38 + "…"  # 39 chars in, 41 chars after replacement
    p = NotificationPayload(title=long, body="ok", success=True)
    assert len(p.title) == 40
    p.title.encode("ascii")


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
