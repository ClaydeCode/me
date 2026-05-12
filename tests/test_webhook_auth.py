import pytest
from fastapi import HTTPException

from clayde.webhook.auth import verify_bearer


def test_verify_bearer_accepts_correct_token():
    verify_bearer("Bearer secret-xyz", expected="secret-xyz")  # no exception


def test_verify_bearer_rejects_missing_header():
    with pytest.raises(HTTPException) as exc:
        verify_bearer(None, expected="secret-xyz")
    assert exc.value.status_code == 401


def test_verify_bearer_rejects_wrong_scheme():
    with pytest.raises(HTTPException) as exc:
        verify_bearer("Basic abc", expected="secret-xyz")
    assert exc.value.status_code == 401


def test_verify_bearer_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc:
        verify_bearer("Bearer wrong", expected="secret-xyz")
    assert exc.value.status_code == 401


def test_verify_bearer_rejects_empty_expected():
    # If the server is misconfigured (no token set), all requests must fail.
    with pytest.raises(HTTPException) as exc:
        verify_bearer("Bearer anything", expected="")
    assert exc.value.status_code == 401
