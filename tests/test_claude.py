"""Tests for clayde.claude."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import anthropic

from clayde.claude import (
    UsageLimitError,
    _calculate_cost_usd,
    _execute_tool,
    invoke_claude,
    is_claude_available,
)


def _mock_settings(model="claude-sonnet-4-6", api_key="test-key"):
    s = MagicMock()
    s.claude_model = model
    s.claude_api_key = api_key
    return s


def _make_tool_use_block(tool_name, tool_id, input_data):
    """Build a mock tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.id = tool_id
    block.input = input_data
    return block


def _make_end_turn_response(text="done", input_tokens=200, output_tokens=100):
    """Build a mock end_turn response."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    response.stop_reason = "end_turn"
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    response._raw_response.headers = {
        "anthropic-ratelimit-requests-remaining": "95",
        "anthropic-ratelimit-tokens-remaining": "45000",
    }
    return response


def _make_tool_response(tool_blocks, input_tokens=150, output_tokens=80):
    """Build a mock tool_use stop response."""
    response = MagicMock()
    response.content = tool_blocks
    response.stop_reason = "tool_use"
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    response._raw_response.headers = {}
    return response


class TestCalculateCostUsd:
    def test_known_model(self):
        # claude-sonnet-4-6: $3/1M input, $15/1M output
        cost = _calculate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.0)

    def test_unknown_model_uses_default(self):
        # unknown model falls back to $3/$15
        cost = _calculate_cost_usd("unknown-model", 1_000_000, 0)
        assert cost == pytest.approx(3.0)

    def test_zero_tokens(self):
        assert _calculate_cost_usd("claude-sonnet-4-6", 0, 0) == 0.0


class TestExecuteTool:
    def test_bash_success(self, tmp_path):
        block = MagicMock()
        block.name = "bash"
        block.input = {"command": "echo hello"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "hello" in result

    def test_bash_stderr_included(self, tmp_path):
        block = MagicMock()
        block.name = "bash"
        block.input = {"command": "echo out; echo err >&2"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "out" in result
        assert "err" in result

    def test_bash_timeout(self, tmp_path):
        block = MagicMock()
        block.name = "bash"
        block.input = {"command": "sleep 1000"}
        with patch("clayde.claude.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("sleep", 300)):
            result = _execute_tool(block, cwd=str(tmp_path))
        assert "timed out" in result

    def test_text_editor_view_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("file contents")
        block = MagicMock()
        block.name = "text_editor"
        block.input = {"command": "view", "path": "test.txt"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert result == "file contents"

    def test_text_editor_view_dir(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        block = MagicMock()
        block.name = "text_editor"
        block.input = {"command": "view", "path": "."}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "a.py" in result
        assert "b.py" in result

    def test_text_editor_create(self, tmp_path):
        block = MagicMock()
        block.name = "text_editor"
        block.input = {"command": "create", "path": "new.txt", "file_text": "hello"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "created" in result.lower()
        assert (tmp_path / "new.txt").read_text() == "hello"

    def test_text_editor_str_replace(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("old text here")
        block = MagicMock()
        block.name = "text_editor"
        block.input = {"command": "str_replace", "path": "edit.txt",
                       "old_str": "old text", "new_str": "new text"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "Replacement done" in result
        assert f.read_text() == "new text here"

    def test_text_editor_str_replace_not_found(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("content")
        block = MagicMock()
        block.name = "text_editor"
        block.input = {"command": "str_replace", "path": "edit.txt",
                       "old_str": "nonexistent", "new_str": "replacement"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "[error:" in result

    def test_unknown_tool_returns_error(self, tmp_path):
        block = MagicMock()
        block.name = "unknown_tool"
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "[error:" in result


class TestInvokeClaude:
    def test_single_turn_end_turn(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")
        end_turn_response = _make_end_turn_response("plan output")
        mock_client = MagicMock()
        mock_client.beta.messages.create.return_value = end_turn_response

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client):
            result = invoke_claude("test prompt", str(tmp_path))

        assert result == "plan output"
        mock_client.beta.messages.create.assert_called_once()

    def test_tool_loop_executes_tools_then_end_turn(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")

        # First call returns a tool_use block
        tool_block = _make_tool_use_block("bash", "tool-1", {"command": "echo done"})
        tool_response = _make_tool_response([tool_block])

        # Second call returns end_turn
        end_response = _make_end_turn_response("finished")

        mock_client = MagicMock()
        mock_client.beta.messages.create.side_effect = [tool_response, end_response]

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude._execute_tool", return_value="tool output") as mock_exec:
            result = invoke_claude("implement", str(tmp_path))

        assert result == "finished"
        assert mock_client.beta.messages.create.call_count == 2
        mock_exec.assert_called_once()

    def test_rate_limit_raises_usage_limit_error(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")
        mock_client = MagicMock()
        mock_client.beta.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limit", response=MagicMock(), body={}
        )

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client):
            with pytest.raises(UsageLimitError):
                invoke_claude("prompt", "/repo")

    def test_overloaded_529_raises_usage_limit_error(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")
        mock_response_obj = MagicMock()
        mock_response_obj.status_code = 529
        mock_client = MagicMock()
        mock_client.beta.messages.create.side_effect = anthropic.APIStatusError(
            message="overloaded", response=mock_response_obj, body={}
        )

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client):
            with pytest.raises(UsageLimitError):
                invoke_claude("prompt", "/repo")

    def test_other_api_error_propagates(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")
        mock_response_obj = MagicMock()
        mock_response_obj.status_code = 500
        mock_client = MagicMock()
        mock_client.beta.messages.create.side_effect = anthropic.APIStatusError(
            message="server error", response=mock_response_obj, body={}
        )

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client):
            with pytest.raises(anthropic.APIStatusError):
                invoke_claude("prompt", "/repo")

    def test_tool_loop_timeout(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")

        # Always return a tool_use block so the loop never ends naturally
        tool_block = _make_tool_use_block("bash", "tool-1", {"command": "echo loop"})
        tool_response = _make_tool_response([tool_block])
        mock_client = MagicMock()
        mock_client.beta.messages.create.return_value = tool_response

        call_count = [0]

        def fake_monotonic():
            call_count[0] += 1
            if call_count[0] <= 1:
                return 0.0
            return 2000.0  # Way past the 1800s deadline

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude._execute_tool", return_value="output"), \
             patch("clayde.claude.time.monotonic", side_effect=fake_monotonic):
            with pytest.raises(TimeoutError):
                invoke_claude("implement", str(tmp_path))

    def test_token_usage_accumulated_across_turns(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")

        tool_block = _make_tool_use_block("bash", "t1", {"command": "echo x"})
        turn1 = _make_tool_response([tool_block], input_tokens=100, output_tokens=50)
        turn2 = _make_end_turn_response("done", input_tokens=200, output_tokens=100)

        mock_client = MagicMock()
        mock_client.beta.messages.create.side_effect = [turn1, turn2]

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude._execute_tool", return_value="x"):
            result = invoke_claude("impl", str(tmp_path))

        assert result == "done"
        assert mock_client.beta.messages.create.call_count == 2


class TestIsClaudeAvailable:
    def test_available_on_success(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock()

        with patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude.get_settings", return_value=_mock_settings("/tmp")):
            assert is_claude_available() is True

    def test_unavailable_on_rate_limit(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limit", response=MagicMock(), body={}
        )

        with patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude.get_settings", return_value=_mock_settings("/tmp")):
            assert is_claude_available() is False

    def test_available_on_other_exception(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = OSError("connection refused")

        with patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude.get_settings", return_value=_mock_settings("/tmp")):
            assert is_claude_available() is True

    def test_available_on_api_error_non_rate_limit(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.messages.create.side_effect = anthropic.APIStatusError(
            message="server error", response=mock_response, body={}
        )

        with patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude.get_settings", return_value=_mock_settings("/tmp")):
            assert is_claude_available() is True
