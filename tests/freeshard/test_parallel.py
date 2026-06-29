"""Tests for the parallelism knob in tick() and the worktree lock."""
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from clayde.freeshard import loop, worktree
from clayde.freeshard.phase import Phase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue(url, number):
    i = MagicMock()
    i.html_url = url
    i.number = number
    return i


def _settings(parallelism=1, reviewer="max"):
    s = MagicMock()
    s.fs_reviewer = reviewer
    s.fs_parallelism = parallelism
    return s


def _common_patches(issues):
    return {
        "get_assigned_issues": MagicMock(return_value=issues),
        "is_non_core": MagicMock(return_value=True),
        "is_blocked": MagicMock(return_value=False),
        "find_open_pr": MagicMock(return_value=None),
        "get_default_branch": MagicMock(return_value="main"),
        "get_ci_status": MagicMock(return_value=None),
        "is_reviewer_assigned": MagicMock(return_value=False),
        "count_fix_commits": MagicMock(return_value=0),
        "derive_phase": MagicMock(return_value=Phase.IMPLEMENT),
        "run_implement": MagicMock(),
        "run_ci_fix": MagicMock(),
        "run_handoff": MagicMock(),
        "run_manual_verify": MagicMock(),
    }


ISSUES = [
    _issue("https://github.com/FreeshardBase/repo-a/issues/1", 1),
    _issue("https://github.com/FreeshardBase/repo-b/issues/2", 2),
    _issue("https://github.com/FreeshardBase/repo-c/issues/3", 3),
]


# ---------------------------------------------------------------------------
# test_parallelism_one_is_serial
# ---------------------------------------------------------------------------

def test_parallelism_one_is_serial():
    patches = _common_patches(ISSUES)
    with patch.multiple("clayde.freeshard.loop", **patches):
        n = loop.tick(MagicMock(), _settings(parallelism=1))
    assert n == 3
    assert patches["run_implement"].call_count == 3


# ---------------------------------------------------------------------------
# test_parallelism_n_processes_all_issues
# ---------------------------------------------------------------------------

def test_parallelism_n_processes_all_issues():
    patches = _common_patches(ISSUES)
    with patch.multiple("clayde.freeshard.loop", **patches):
        n = loop.tick(MagicMock(), _settings(parallelism=3))
    assert n == 3
    assert patches["run_implement"].call_count == 3


# ---------------------------------------------------------------------------
# test_worktree_add_holds_lock
# ---------------------------------------------------------------------------

def test_worktree_add_holds_lock():
    assert hasattr(worktree, "_worktree_lock"), "module-level _worktree_lock missing"
    assert hasattr(worktree._worktree_lock, "acquire"), "_worktree_lock is not a Lock"

    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with patch.object(worktree, "_worktree_lock", mock_lock):
        with patch("clayde.freeshard.worktree.ensure_repo", return_value=MagicMock()) as mock_ensure:
            with patch("clayde.freeshard.worktree.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch("pathlib.Path.exists", return_value=False):
                    with patch("pathlib.Path.mkdir"):
                        worktree.add_worktree("o", "r", 7, "main")

    mock_lock.__enter__.assert_called_once()
    mock_lock.__exit__.assert_called_once()
    mock_ensure.assert_called_once()


# ---------------------------------------------------------------------------
# test_parallelism_serial_isolates_failing_issue  (I-1 serial)
# ---------------------------------------------------------------------------

def test_parallelism_serial_isolates_failing_issue():
    """One raising handler does not abort remaining issues in serial mode."""
    issues = [
        _issue("https://github.com/FreeshardBase/repo-a/issues/1", 1),
        _issue("https://github.com/FreeshardBase/repo-b/issues/2", 2),
        _issue("https://github.com/FreeshardBase/repo-c/issues/3", 3),
    ]

    def _raise_on_2(g, owner, repo, number, default_branch):
        if number == 2:
            raise RuntimeError("boom")

    patches = _common_patches(issues)
    patches["run_implement"] = MagicMock(side_effect=_raise_on_2)

    with patch.multiple("clayde.freeshard.loop", **patches):
        n = loop.tick(MagicMock(), _settings(parallelism=1))

    assert n == 2
    assert patches["run_implement"].call_count == 3


# ---------------------------------------------------------------------------
# test_parallelism_parallel_isolates_failing_issue  (I-1 parallel)
# ---------------------------------------------------------------------------

def test_parallelism_parallel_isolates_failing_issue():
    """One raising handler does not abort remaining issues in parallel mode."""
    issues = [
        _issue("https://github.com/FreeshardBase/repo-a/issues/1", 1),
        _issue("https://github.com/FreeshardBase/repo-b/issues/2", 2),
        _issue("https://github.com/FreeshardBase/repo-c/issues/3", 3),
    ]

    def _raise_on_2(g, owner, repo, number, default_branch):
        if number == 2:
            raise RuntimeError("boom")

    patches = _common_patches(issues)
    patches["run_implement"] = MagicMock(side_effect=_raise_on_2)

    with patch.multiple("clayde.freeshard.loop", **patches):
        n = loop.tick(MagicMock(), _settings(parallelism=3))

    assert n == 2
    assert patches["run_implement"].call_count == 3


# ---------------------------------------------------------------------------
# test_parallelism_bounds_concurrency  (I-2)
# ---------------------------------------------------------------------------

def test_parallelism_bounds_concurrency():
    """ThreadPoolExecutor with fs_parallelism=2 caps peak concurrent handlers at 2."""
    issues = [
        _issue(f"https://github.com/FreeshardBase/repo-x/issues/{i}", i)
        for i in range(1, 6)
    ]

    counter_lock = threading.Lock()
    active = [0]
    peak = [0]

    def _tracking_handler(g, owner, repo, number, default_branch):
        with counter_lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.01)
        with counter_lock:
            active[0] -= 1

    patches = _common_patches(issues)
    patches["run_implement"] = MagicMock(side_effect=_tracking_handler)

    with patch.multiple("clayde.freeshard.loop", **patches):
        n = loop.tick(MagicMock(), _settings(parallelism=2))

    assert n == 5
    assert peak[0] <= 2
