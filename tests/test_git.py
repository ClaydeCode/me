"""Tests for clayde.git."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import clayde.git as git_mod


def _mock_settings(repos_dir):
    s = MagicMock()
    s.repos_dir = Path(repos_dir)
    return s


class TestEnsureRepo:
    def test_clones_when_no_git_dir(self, tmp_path):
        repo_path = tmp_path / "alice__myrepo"

        with patch("clayde.git.get_settings", return_value=_mock_settings(tmp_path)), \
             patch("clayde.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = git_mod.ensure_repo("alice", "myrepo", "main")

        assert result == repo_path
        mock_run.assert_called_once_with(
            ["git", "clone", "https://github.com/alice/myrepo.git", str(repo_path)],
            capture_output=True, text=True,
        )

    def test_updates_when_git_dir_exists(self, tmp_path):
        repo_path = tmp_path / "alice__myrepo"
        (repo_path / ".git").mkdir(parents=True)

        with patch("clayde.git.get_settings", return_value=_mock_settings(tmp_path)), \
             patch("clayde.git.subprocess.run") as mock_run:
            result = git_mod.ensure_repo("alice", "myrepo", "main")

        assert result == repo_path
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["git", "checkout", "main"], cwd=repo_path, capture_output=True,
        )
        mock_run.assert_any_call(
            ["git", "pull"], cwd=repo_path, capture_output=True,
        )

    def test_clone_failure_raises(self, tmp_path):
        with patch("clayde.git.get_settings", return_value=_mock_settings(tmp_path)), \
             patch("clayde.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="fatal: not found")
            with pytest.raises(RuntimeError, match="Clone failed"):
                git_mod.ensure_repo("alice", "myrepo", "main")
