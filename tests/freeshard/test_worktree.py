from unittest.mock import patch, MagicMock
from pathlib import Path
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
