"""Tests for clayde.orchestrator — Pebble webhook host."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_run_loop_with_pebble_invokes_async_entry(monkeypatch):
    """run_loop() must hand off to the async Pebble entry point."""
    from clayde import orchestrator

    invoked = {}

    async def fake_async_main():
        invoked["called"] = True

    monkeypatch.setattr(orchestrator, "_run_with_pebble", fake_async_main)
    orchestrator.run_loop()
    assert invoked.get("called") is True


def test_freeshard_loop_runs_inside_pebble_gather(monkeypatch):
    """_freeshard_loop is gathered with the Pebble server; run_cycle is invoked and _shutdown stops it."""
    from clayde import orchestrator

    cycle_calls = []

    def fake_run_cycle(settings):
        cycle_calls.append(True)
        orchestrator._shutdown = True  # stop after one cycle
        return 1

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def fake_serve():
        pass

    async def fake_worker_loop(queue, *, timeout_s, kb_path):
        pass

    monkeypatch.setattr(orchestrator, "_shutdown", False)

    mock_settings = MagicMock()
    mock_settings.pebble_port = 8080
    mock_settings.pebble_queue_max = 10
    mock_settings.pebble_token = "tok"
    mock_settings.pebble_timeout = 60
    mock_settings.kb_path = "/kb"
    mock_settings.fs_loop_interval_s = 0

    with (
        patch("clayde.orchestrator.setup_logging"),
        patch("clayde.orchestrator.get_settings", return_value=mock_settings),
        patch("clayde.orchestrator.create_app", return_value=MagicMock()),
        patch("clayde.orchestrator.JobQueue"),
        patch("clayde.orchestrator.uvicorn") as mock_uvicorn,
        patch("clayde.orchestrator.worker_loop", new=fake_worker_loop),
        patch("clayde.orchestrator.run_cycle", new=fake_run_cycle),
        patch("asyncio.to_thread", new=fake_to_thread),
    ):
        mock_uvicorn.Config.return_value = MagicMock()
        mock_uvicorn.Server.return_value.serve = fake_serve
        asyncio.run(orchestrator._run_with_pebble())

    assert cycle_calls, "run_cycle must be invoked via the freeshard loop"
