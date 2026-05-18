"""ntfy.sh notification dispatch for Pebble webhook terminal outcomes.

Best-effort: any HTTP / network failure is logged but never raised.
A notification is feedback, not a transactional side-effect.
"""

from __future__ import annotations

import logging
from email.header import Header

import httpx
from pydantic import BaseModel, field_validator

from clayde.telemetry import get_tracer

log = logging.getLogger("clayde.webhook.notify")


def _encode_header_value(text: str) -> str:
    """Encode a header value safely for httpx.

    httpx serialises header values as latin-1, so raw non-ASCII strings
    raise UnicodeEncodeError before the request leaves the process. ntfy
    accepts RFC 2047 encoded-words (``=?utf-8?b?<base64>?=``) and decodes
    them server-side, so we route non-ASCII through that. ASCII titles
    pass through verbatim — keeps log/trace output readable and avoids
    pointless wire overhead.
    """
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return Header(text, charset="utf-8").encode()
    return text


class NotificationPayload(BaseModel):
    """Outcome of a Pebble run, as emitted by Claude in the JSON tail.

    Title is clamped to 40 chars and body to 300 chars at construction
    time so accidental over-long values never propagate to ntfy. The
    title is stored as the raw Unicode string the user/Claude produced;
    RFC 2047 encoding for the actual HTTP header happens in ``send_ntfy``.
    """

    title: str
    body: str
    success: bool

    @field_validator("title", mode="before")
    @classmethod
    def _clamp_title(cls, v):
        return v[:40] if isinstance(v, str) else v

    @field_validator("body", mode="before")
    @classmethod
    def _clamp_body(cls, v):
        return v[:300] if isinstance(v, str) else v


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
        "Title": _encode_header_value(title),
        "Priority": "3" if success else "5",
        "Tags": "white_check_mark" if success else "rotating_light",
    }
    tracer = get_tracer()
    with tracer.start_as_current_span("clayde.pebble.notify") as span:
        span.set_attribute("pebble.notify_topic", topic)
        # Span attribute holds the raw Unicode title for readable traces.
        span.set_attribute("pebble.notify_title", title)
        span.set_attribute("pebble.outcome_success", success)
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, content=body, headers=headers)
            span.set_attribute("pebble.notify_http_status", resp.status_code)
            span.set_attribute("pebble.notify_ok", 200 <= resp.status_code < 300)
            if resp.status_code >= 400:
                log.warning("ntfy returned %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            span.set_attribute("pebble.notify_ok", False)
            span.set_attribute("pebble.notify_error", type(exc).__name__)
            log.warning("ntfy POST failed: %s", exc)
