"""Bearer-token verification for the Pebble webhook."""

from __future__ import annotations

import secrets

from fastapi import HTTPException


def verify_bearer(authorization: str | None, *, expected: str) -> None:
    """Verify an ``Authorization: Bearer <token>`` header.

    Raises ``HTTPException(401)`` for missing header, wrong scheme,
    wrong token, or missing server-side token (misconfiguration).
    Comparison is constant-time.
    """
    if not expected:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    provided = authorization[len("Bearer "):]
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
