"""Tests for the always-on Freeshard entry point (run_loop)."""
from unittest.mock import MagicMock, patch

import pytest

from clayde.freeshard import entry


def _mock_settings(interval: int = 1) -> MagicMock:
    s = MagicMock()
    s.fs_loop_interval_s = interval
    return s


@pytest.fixture(autouse=True)
def reset_shutdown():
    entry._shutdown = False
    yield
    entry._shutdown = False


def test_run_loop_calls_run_cycle_then_stops():
    """run_cycle() is called once; loop exits when it sets _shutdown."""

    def cycle_side_effect(settings):
        entry._shutdown = True
        return 1

    with (
        patch("clayde.freeshard.entry.setup_logging"),
        patch("clayde.freeshard.entry.get_settings", return_value=_mock_settings()),
        patch("clayde.freeshard.entry.run_cycle", side_effect=cycle_side_effect) as mock_cycle,
        patch("time.sleep"),
    ):
        entry.run_loop()

    mock_cycle.assert_called_once()


def test_run_loop_skips_cycle_when_shutdown_pre_set():
    """If _shutdown is True before run_cycle, loop exits without calling it."""
    entry._shutdown = True

    with (
        patch("clayde.freeshard.entry.setup_logging"),
        patch("clayde.freeshard.entry.get_settings", return_value=_mock_settings()),
        patch("clayde.freeshard.entry.run_cycle") as mock_cycle,
    ):
        entry.run_loop()

    mock_cycle.assert_not_called()


def test_run_loop_continues_after_cycle_exception():
    """Loop survives bad cycle — exception caught, loop continues, next cycle runs."""

    call_count = 0

    def cycle_side_effect(settings):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        entry._shutdown = True
        return 0

    with (
        patch("clayde.freeshard.entry.setup_logging"),
        patch("clayde.freeshard.entry.get_settings", return_value=_mock_settings()),
        patch("clayde.freeshard.entry.run_cycle", side_effect=cycle_side_effect) as mock_cycle,
        patch("time.sleep"),
    ):
        entry.run_loop()

    assert mock_cycle.call_count == 2
