"""Tests for clayde.claude."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clayde.claude import (
    UsageLimitError,
    _is_limit_error,
    _make_env,
    invoke_claude,
    is_claude_available,
)


def _mock_settings(dir_path):
    s = MagicMock()
    s.dir = Path(dir_path)
    return s


class TestIsLimitError:
    def test_detects_usage_limit(self):
        assert _is_limit_error("You have hit your usage limit") is True

    def test_detects_rate_limit(self):
        assert _is_limit_error("Rate limit exceeded") is True

    def test_detects_session_limit(self):
        assert _is_limit_error("Session limit reached") is True

    def test_detects_youve_reached(self):
        assert _is_limit_error("You've reached the maximum") is True

    def test_detects_exceeded_your(self):
        assert _is_limit_error("You have exceeded your quota") is True

    def test_detects_claude_code_pro(self):
        assert _is_limit_error("Claude Code Pro plan limit") is True

    def test_case_insensitive(self):
        assert _is_limit_error("USAGE LIMIT hit") is True

    def test_no_match(self):
        assert _is_limit_error("Everything is fine") is False

    def test_empty_string(self):
        assert _is_limit_error("") is False


class TestMakeEnv:
    def test_removes_claudecode(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "1")
        env = _make_env()
        assert "CLAUDECODE" not in env

    def test_preserves_other_vars(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        env = _make_env()
        assert env["MY_VAR"] == "hello"

    def test_no_claudecode_present(self):
        env = _make_env()
        assert "CLAUDECODE" not in env


class TestInvokeClaude:
    def test_success(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity text")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "plan output"
        mock_result.stderr = ""

        with patch("clayde.claude.get_settings", return_value=_mock_settings(tmp_path)), \
             patch("clayde.claude.subprocess.run", return_value=mock_result) as mock_run:
            result = invoke_claude("test prompt", "/some/repo")

        assert result == "plan output"
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][0][2] == "test prompt"
        assert args[1]["cwd"] == "/some/repo"

    def test_nonzero_exit_without_limit(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "partial output"
        mock_result.stderr = "some error"

        with patch("clayde.claude.get_settings", return_value=_mock_settings(tmp_path)), \
             patch("clayde.claude.subprocess.run", return_value=mock_result):
            result = invoke_claude("prompt", "/repo")

        assert result == "partial output"

    def test_nonzero_exit_with_limit_raises(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "You have hit your usage limit"

        with patch("clayde.claude.get_settings", return_value=_mock_settings(tmp_path)), \
             patch("clayde.claude.subprocess.run", return_value=mock_result):
            with pytest.raises(UsageLimitError):
                invoke_claude("prompt", "/repo")

    def test_exit_zero_with_limit_in_stdout_raises(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Some output... you've reached the limit"
        mock_result.stderr = ""

        with patch("clayde.claude.get_settings", return_value=_mock_settings(tmp_path)), \
             patch("clayde.claude.subprocess.run", return_value=mock_result):
            with pytest.raises(UsageLimitError):
                invoke_claude("prompt", "/repo")

    def test_returns_empty_string_on_none_stdout(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = None
        mock_result.stderr = None

        with patch("clayde.claude.get_settings", return_value=_mock_settings(tmp_path)), \
             patch("clayde.claude.subprocess.run", return_value=mock_result):
            result = invoke_claude("prompt", "/repo")
        assert result == ""


class TestIsClaudeAvailable:
    def test_available(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""

        with patch("clayde.claude.subprocess.run", return_value=mock_result):
            assert is_claude_available() is True

    def test_unavailable_nonzero_with_limit(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "usage limit"

        with patch("clayde.claude.subprocess.run", return_value=mock_result):
            assert is_claude_available() is False

    def test_unavailable_zero_with_limit_in_stdout(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "you've reached the limit"
        mock_result.stderr = ""

        with patch("clayde.claude.subprocess.run", return_value=mock_result):
            assert is_claude_available() is False

    def test_exception_returns_true(self):
        with patch("clayde.claude.subprocess.run", side_effect=OSError("not found")):
            assert is_claude_available() is True
