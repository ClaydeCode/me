"""Tests for the always-on Freeshard entry point (run_loop)."""
from unittest.mock import MagicMock, patch

import pytest

from clayde.freeshard import entry


def _mock_settings(interval: int = 1) -> MagicMock:
    s = MagicMock()
    s.github_token = "token"
    s.effective_git_name = "ClaydeCode"
    s.git_email = "clayde@example.com"
    s.fs_loop_interval_s = interval
    return s


@pytest.fixture(autouse=True)
def reset_shutdown():
    entry._shutdown = False
    yield
    entry._shutdown = False


def test_run_loop_calls_tick_then_stops():
    """tick() is called once; loop exits when tick sets _shutdown."""

    def tick_side_effect(g, settings):
        entry._shutdown = True
        return 1

    with (
        patch("clayde.freeshard.entry.setup_logging"),
        patch("clayde.freeshard.entry.get_settings", return_value=_mock_settings()),
        patch("clayde.freeshard.entry.subprocess.run"),
        patch("clayde.freeshard.entry.is_claude_available", return_value=True),
        patch("clayde.freeshard.entry.get_github_client", return_value=MagicMock()),
        patch("clayde.freeshard.entry.tick", side_effect=tick_side_effect) as mock_tick,
        patch("time.sleep"),
    ):
        entry.run_loop()

    mock_tick.assert_called_once()


def test_run_loop_skips_tick_when_claude_unavailable():
    """tick() is never called when Claude is unavailable; loop exits on first sleep."""

    def sleep_side_effect(_duration):
        entry._shutdown = True

    with (
        patch("clayde.freeshard.entry.setup_logging"),
        patch("clayde.freeshard.entry.get_settings", return_value=_mock_settings(interval=1)),
        patch("clayde.freeshard.entry.subprocess.run"),
        patch("clayde.freeshard.entry.is_claude_available", return_value=False),
        patch("clayde.freeshard.entry.tick") as mock_tick,
        patch("time.sleep", side_effect=sleep_side_effect),
    ):
        entry.run_loop()

    mock_tick.assert_not_called()


def test_run_loop_continues_after_tick_exception():
    """Loop survives bad tick — exception caught, loop continues, next tick runs."""

    call_count = 0

    def tick_side_effect(g, settings):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        else:
            entry._shutdown = True
            return 0

    with (
        patch("clayde.freeshard.entry.setup_logging"),
        patch("clayde.freeshard.entry.get_settings", return_value=_mock_settings()),
        patch("clayde.freeshard.entry.subprocess.run"),
        patch("clayde.freeshard.entry.is_claude_available", return_value=True),
        patch("clayde.freeshard.entry.get_github_client", return_value=MagicMock()),
        patch("clayde.freeshard.entry.tick", side_effect=tick_side_effect) as mock_tick,
        patch("time.sleep"),
    ):
        entry.run_loop()

    assert mock_tick.call_count == 2
