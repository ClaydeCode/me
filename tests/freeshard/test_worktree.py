from unittest.mock import patch, MagicMock, call
from pathlib import Path
import pytest
from clayde.freeshard import worktree

@patch("pathlib.Path.mkdir")
@patch("clayde.freeshard.worktree.subprocess.run")
@patch("clayde.freeshard.worktree.ensure_repo")
def test_add_worktree_creates_branch_worktree(mock_ensure, mock_run, mock_mkdir):
    mock_ensure.return_value = Path("/data/repos/o__r")
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    p = worktree.add_worktree("o", "r", 7, "main")
    assert p == worktree.WORKTREES_DIR / "o__r__issue-7"
    assert any("worktree" in c.args[0] for c in mock_run.call_args_list)


@patch("pathlib.Path.exists")
@patch("pathlib.Path.mkdir")
@patch("clayde.freeshard.worktree.subprocess.run")
@patch("clayde.freeshard.worktree.ensure_repo")
def test_add_worktree_reuses_existing_worktree(mock_ensure, mock_run, mock_mkdir, mock_exists):
    """When .git exists, reuse worktree: fetch + checkout, no worktree add."""
    mock_ensure.return_value = Path("/data/repos/o__r")
    mock_exists.return_value = True
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    p = worktree.add_worktree("o", "r", 7, "main")

    assert p == worktree.WORKTREES_DIR / "o__r__issue-7"
    # Should have fetch and checkout calls, but no worktree add
    calls = mock_run.call_args_list
    assert any("fetch" in c.args[0] for c in calls)
    assert any("checkout" in c.args[0] for c in calls)
    assert not any("worktree" in c.args[0] for c in calls)


@patch("pathlib.Path.exists")
@patch("pathlib.Path.mkdir")
@patch("clayde.freeshard.worktree.subprocess.run")
@patch("clayde.freeshard.worktree.ensure_repo")
def test_add_worktree_remote_branch_uses_B_flag(mock_ensure, mock_run, mock_mkdir, mock_exists):
    """When .git missing and ls-remote finds remote branch, use -B flag."""
    mock_ensure.return_value = Path("/data/repos/o__r")
    mock_exists.return_value = False

    # Mock subprocess.run: first call (ls-remote) returns success with non-empty stdout,
    # second call (worktree add) returns success.
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="abc123def456  refs/heads/clayde/issue-7\n", stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]

    p = worktree.add_worktree("o", "r", 7, "main")

    assert p == worktree.WORKTREES_DIR / "o__r__issue-7"
    # Check that ls-remote was called with the clone URL
    ls_calls = [c for c in mock_run.call_args_list if "ls-remote" in c.args[0]]
    assert len(ls_calls) == 1
    assert "https://github.com/o/r.git" in ls_calls[0].args[0]
    # Check that worktree add was called with -B flag
    worktree_calls = [c for c in mock_run.call_args_list if "worktree" in c.args[0]]
    assert len(worktree_calls) == 1
    assert "-B" in worktree_calls[0].args[0]
    assert "origin/clayde/issue-7" in worktree_calls[0].args[0]


@patch("pathlib.Path.mkdir")
@patch("clayde.freeshard.worktree.subprocess.run")
@patch("clayde.freeshard.worktree.ensure_repo")
def test_remove_worktree_calls_force_remove(mock_ensure, mock_run, mock_mkdir):
    """Verify remove_worktree calls git worktree remove --force."""
    mock_ensure.return_value = Path("/data/repos/o__r")
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    worktree.remove_worktree("o", "r", 7)

    # Check that worktree remove --force was called
    remove_calls = [c for c in mock_run.call_args_list if "worktree" in c.args[0] and "remove" in c.args[0]]
    assert len(remove_calls) == 1
    assert "--force" in remove_calls[0].args[0]


@patch("pathlib.Path.exists")
@patch("pathlib.Path.mkdir")
@patch("clayde.freeshard.worktree.subprocess.run")
@patch("clayde.freeshard.worktree.ensure_repo")
def test_add_worktree_raises_on_ls_remote_failure(mock_ensure, mock_run, mock_mkdir, mock_exists):
    """When ls-remote fails (non-zero return), raise RuntimeError."""
    mock_ensure.return_value = Path("/data/repos/o__r")
    mock_exists.return_value = False
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Permission denied")

    with pytest.raises(RuntimeError, match="ls-remote failed for clayde/issue-7"):
        worktree.add_worktree("o", "r", 7, "main")


@patch("pathlib.Path.exists")
@patch("pathlib.Path.mkdir")
@patch("clayde.freeshard.worktree.subprocess.run")
@patch("clayde.freeshard.worktree.ensure_repo")
def test_add_worktree_first_run_uses_remote_url_for_ls_remote(mock_ensure, mock_run, mock_mkdir, mock_exists):
    """On first run, ls-remote uses remote URL directly (no local base_path required)."""
    mock_ensure.return_value = Path("/data/repos/o__r")
    mock_exists.return_value = False

    # Mock two calls: ls-remote (returns remote branch exists), then worktree add (succeeds).
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="def789abc123  refs/heads/clayde/issue-42\n", stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]

    p = worktree.add_worktree("o", "r", 42, "main")

    assert p == worktree.WORKTREES_DIR / "o__r__issue-42"
    # Verify ls-remote was called with the clone URL (no cwd dependency)
    ls_calls = [c for c in mock_run.call_args_list if "ls-remote" in c.args[0]]
    assert len(ls_calls) == 1
    ls_call = ls_calls[0]
    # Check that the clone URL is in the arguments
    assert "https://github.com/o/r.git" in ls_call.args[0]
    # Verify cwd is not set for ls-remote (not in kwargs, or explicitly None)
    assert "cwd" not in ls_call.kwargs or ls_call.kwargs.get("cwd") is None
