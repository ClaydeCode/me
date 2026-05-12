"""FastAPI app, payload model, and routes for the Pebble webhook."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from clayde.telemetry import get_tracer
from clayde.webhook.auth import verify_bearer
from clayde.webhook.queue import JobQueue, PebbleJob, QueueFullError

log = logging.getLogger("clayde.webhook")


class PebblePayload(BaseModel):
    text: str
    timestamp: int


def create_app(*, queue: JobQueue, expected_token: str) -> FastAPI:
    """Build a FastAPI app bound to the given queue and bearer token."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/webhook/pebble")
    async def receive(
        payload: PebblePayload,
        authorization: str | None = Header(default=None),
    ):
        verify_bearer(authorization, expected=expected_token)

        job_id = str(uuid.uuid4())
        job = PebbleJob(id=job_id, text=payload.text, timestamp=payload.timestamp)

        tracer = get_tracer()
        with tracer.start_as_current_span("clayde.pebble.enqueue") as span:
            span.set_attribute("pebble.job_id", job_id)
            span.set_attribute("pebble.text", payload.text)
            span.set_attribute("pebble.text_len", len(payload.text))
            span.set_attribute("pebble.timestamp", payload.timestamp)
            try:
                queue.enqueue(job)
            except QueueFullError:
                span.set_attribute("http.status_code", 503)
                log.warning("[%s] queue full — rejecting", job_id)
                return JSONResponse(
                    status_code=503,
                    content={"queued": False, "reason": "full"},
                )
            span.set_attribute("http.status_code", 200)
            log.info(
                "[%s] enqueued (text_len=%d, ts=%d)",
                job_id, len(payload.text), payload.timestamp,
            )
            return {"queued": True, "id": job_id}

    return app
