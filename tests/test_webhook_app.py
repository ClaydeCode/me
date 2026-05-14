import pytest
from fastapi.testclient import TestClient

from clayde.webhook.app import PebblePayload, create_app
from clayde.webhook.queue import JobQueue


@pytest.fixture
def queue():
    return JobQueue(maxsize=2)


@pytest.fixture
def client(queue):
    app = create_app(queue=queue, expected_token="test-token")
    return TestClient(app)


def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_post_unknown_path_returns_404(client):
    r = client.post("/webhook/unknown", json={"text": "x", "timestamp": 1})
    assert r.status_code == 404


def test_pebble_accepts_valid_request(client, queue):
    r = client.post(
        "/webhook/pebble",
        json={"text": "hello", "timestamp": 1778068506},
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] is True
    assert "id" in body and isinstance(body["id"], str) and len(body["id"]) > 0


def test_pebble_rejects_missing_token(client):
    r = client.post(
        "/webhook/pebble",
        json={"text": "hi", "timestamp": 1},
    )
    assert r.status_code == 401


def test_pebble_rejects_wrong_token(client):
    r = client.post(
        "/webhook/pebble",
        json={"text": "hi", "timestamp": 1},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_pebble_rejects_bad_payload(client):
    r = client.post(
        "/webhook/pebble",
        json={"text": "hi"},  # missing timestamp
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 422


def test_pebble_returns_503_when_full(queue, monkeypatch):
    # Stub the ntfy dispatcher so the 503 branch does not hit the real network.
    from clayde.webhook import app as app_mod

    async def _noop(**_):
        return None

    monkeypatch.setattr(app_mod, "send_ntfy", _noop)

    # Fill the queue using a smaller capacity so 503 is reachable.
    small = JobQueue(maxsize=1)
    app = create_app(queue=small, expected_token="t")
    client = TestClient(app)
    headers = {"Authorization": "Bearer t"}
    r1 = client.post("/webhook/pebble", json={"text": "a", "timestamp": 1}, headers=headers)
    r2 = client.post("/webhook/pebble", json={"text": "b", "timestamp": 2}, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 503
    assert r2.json() == {"queued": False, "reason": "full"}


@pytest.mark.asyncio
async def test_queue_full_emits_ntfy(monkeypatch):
    from clayde.webhook import app as app_mod
    from clayde.webhook.queue import JobQueue, QueueFullError

    calls = []

    async def fake_send(*, title, body, success, **_):
        calls.append((title, success))

    monkeypatch.setattr(app_mod, "send_ntfy", fake_send)

    class FullQueue(JobQueue):
        def enqueue(self, job):
            raise QueueFullError()

    q = FullQueue(maxsize=1)
    application = app_mod.create_app(queue=q, expected_token="tok")

    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/webhook/pebble",
            json={"text": "hi", "timestamp": 1},
            headers={"Authorization": "Bearer tok"},
        )
    assert resp.status_code == 503
    assert len(calls) == 1
    assert calls[0][0] == "Pebble: queue full"
    assert calls[0][1] is False
