"""Tests for the ntfy notification dispatcher."""

from __future__ import annotations

import base64
import re

import httpx
import pytest
import respx

from clayde.webhook.notify import NotificationPayload, _encode_header_value, send_ntfy


def test_notification_payload_clamps_length():
    p = NotificationPayload(title="x" * 100, body="y" * 1000, success=True)
    assert len(p.title) == 40
    assert len(p.body) == 300


def test_notification_payload_clamps_length_with_unicode():
    # Character-count clamp, not byte-count — verify multibyte chars still
    # count as one position.
    p = NotificationPayload(title="ü" * 100, body="ß" * 1000, success=True)
    assert len(p.title) == 40
    assert len(p.body) == 300
    assert p.title == "ü" * 40


def test_notification_payload_accepts_short():
    p = NotificationPayload(title="hi", body="all good", success=True)
    assert p.title == "hi"
    assert p.body == "all good"
    assert p.success is True


def test_notification_payload_preserves_unicode():
    # Raw Unicode is kept as-is; RFC 2047 encoding happens in send_ntfy.
    p = NotificationPayload(title="Müller — Notiz", body="ok", success=True)
    assert p.title == "Müller — Notiz"


def test_encode_header_value_passes_ascii_through():
    assert _encode_header_value("plain ascii") == "plain ascii"


_RFC2047_WORD = re.compile(r"=\?utf-8\?[bq]\?[^?]*\?=", re.IGNORECASE)


def test_encode_header_value_rfc2047_encodes_unicode():
    out = _encode_header_value("Thomas Stegger — plant prefs saved")
    # email.header.Header emits =?utf-8?[bq]?...?= encoded words; B and Q
    # are both valid RFC 2047 forms and ntfy decodes either.
    assert _RFC2047_WORD.search(out)
    decoded = _decode_rfc2047(out)
    assert decoded == "Thomas Stegger — plant prefs saved"
    # Result must be ASCII-only so httpx can serialise it as a header.
    out.encode("ascii")


def _decode_rfc2047(encoded: str) -> str:
    """Decode an RFC 2047 encoded-word string back to its Unicode form."""
    from email.header import decode_header
    parts = decode_header(encoded)
    out = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(charset or "ascii"))
        else:
            out.append(chunk)
    return "".join(out)


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
    # ASCII title passes through verbatim.
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
async def test_send_ntfy_encodes_unicode_title_as_rfc2047():
    route = respx.post("https://ntfy.sh/abc123").mock(
        return_value=httpx.Response(200, json={"id": "msg1"})
    )
    title = "Thomas Stegger — plant prefs saved"
    await send_ntfy(
        title=title,
        body="ok",
        success=True,
        base_url="https://ntfy.sh",
        topic="abc123",
        timeout_s=5,
    )
    req = route.calls.last.request
    header = req.headers["title"]
    # Must be ASCII-only so httpx can transmit it.
    header.encode("ascii")
    assert _RFC2047_WORD.search(header)
    assert _decode_rfc2047(header) == title


@pytest.mark.asyncio
@respx.mock
async def test_send_ntfy_handles_emoji_title():
    route = respx.post("https://ntfy.sh/abc123").mock(
        return_value=httpx.Response(200, json={"id": "msg1"})
    )
    title = "\U0001f600 done"
    await send_ntfy(
        title=title,
        body="ok",
        success=True,
        base_url="https://ntfy.sh",
        topic="abc123",
        timeout_s=5,
    )
    req = route.calls.last.request
    header = req.headers["title"]
    header.encode("ascii")
    assert _decode_rfc2047(header) == title


@pytest.mark.asyncio
@respx.mock
async def test_send_ntfy_handles_german_umlauts_title():
    route = respx.post("https://ntfy.sh/abc123").mock(
        return_value=httpx.Response(200, json={"id": "msg1"})
    )
    title = "Müller — Notiz gespeichert"
    await send_ntfy(
        title=title,
        body="ok",
        success=True,
        base_url="https://ntfy.sh",
        topic="abc123",
        timeout_s=5,
    )
    req = route.calls.last.request
    assert _decode_rfc2047(req.headers["title"]) == title


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
