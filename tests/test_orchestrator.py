"""Tests for clayde.orchestrator — Pebble webhook host."""

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
