"""Tests for clayde.claude."""

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import anthropic

from clayde.claude import (
    InvocationResult,
    UsageLimitError,
    _calculate_cost_usd,
    _execute_tool,
    _load_conversation,
    _save_conversation,
    _serialize_messages,
    format_cost_line,
    invoke_claude,
    is_claude_available,
)
from clayde.git import commit_wip


def _mock_settings(model="claude-sonnet-4-6", api_key="test-key"):
    s = MagicMock()
    s.claude_model = model
    s.claude_api_key = api_key
    s.claude_tool_loop_timeout_s = 1800
    s.claude_bash_timeout_s = 300
    s.claude_max_tokens = 8192
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


class TestInvocationResult:
    def test_construction(self):
        result = InvocationResult(output="hello", cost_eur=1.23, input_tokens=100, output_tokens=50)
        assert result.output == "hello"
        assert result.cost_eur == 1.23
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    def test_zero_cost(self):
        result = InvocationResult(output="", cost_eur=0.0, input_tokens=0, output_tokens=0)
        assert result.cost_eur == 0.0


class TestUsageLimitErrorCost:
    def test_default_cost_is_zero(self):
        e = UsageLimitError("limit hit")
        assert e.cost_eur == 0.0
        assert str(e) == "limit hit"

    def test_carries_partial_cost(self):
        e = UsageLimitError("limit hit", cost_eur=2.50)
        assert e.cost_eur == 2.50

    def test_backward_compatible_raise(self):
        """Old code that raises UsageLimitError('msg') still works."""
        with pytest.raises(UsageLimitError) as exc_info:
            raise UsageLimitError("old style")
        assert exc_info.value.cost_eur == 0.0


class TestFormatCostLine:
    def test_zero_cost(self):
        assert format_cost_line(0.0) == "\n\n💸 This task cost 0.00€"

    def test_small_cost(self):
        assert format_cost_line(0.01) == "\n\n💸 This task cost 0.01€"

    def test_normal_cost(self):
        assert format_cost_line(2.34) == "\n\n💸 This task cost 2.34€"

    def test_large_cost(self):
        assert format_cost_line(15.678) == "\n\n💸 This task cost 15.68€"


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
        block.name = "str_replace_based_edit_tool"
        block.input = {"command": "view", "path": "test.txt"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert result == "file contents"

    def test_text_editor_view_dir(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        block = MagicMock()
        block.name = "str_replace_based_edit_tool"
        block.input = {"command": "view", "path": "."}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "a.py" in result
        assert "b.py" in result

    def test_text_editor_create(self, tmp_path):
        block = MagicMock()
        block.name = "str_replace_based_edit_tool"
        block.input = {"command": "create", "path": "new.txt", "file_text": "hello"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "created" in result.lower()
        assert (tmp_path / "new.txt").read_text() == "hello"

    def test_text_editor_str_replace(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("old text here")
        block = MagicMock()
        block.name = "str_replace_based_edit_tool"
        block.input = {"command": "str_replace", "path": "edit.txt",
                       "old_str": "old text", "new_str": "new text"}
        result = _execute_tool(block, cwd=str(tmp_path))
        assert "Replacement done" in result
        assert f.read_text() == "new text here"

    def test_text_editor_str_replace_not_found(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("content")
        block = MagicMock()
        block.name = "str_replace_based_edit_tool"
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

        assert isinstance(result, InvocationResult)
        assert result.output == "plan output"
        assert result.cost_eur >= 0.0
        assert result.input_tokens == 200
        assert result.output_tokens == 100
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

        assert result.output == "finished"
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
            with pytest.raises(UsageLimitError) as exc_info:
                invoke_claude("prompt", "/repo")
            # No tokens consumed before the first API call, so cost should be 0
            assert exc_info.value.cost_eur == 0.0

    def test_rate_limit_after_tool_use_carries_partial_cost(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")

        # First call succeeds with tool use, second call hits rate limit
        tool_block = _make_tool_use_block("bash", "t1", {"command": "echo x"})
        tool_response = _make_tool_response([tool_block], input_tokens=1000, output_tokens=500)

        mock_client = MagicMock()
        mock_client.beta.messages.create.side_effect = [
            tool_response,
            anthropic.RateLimitError(message="rate limit", response=MagicMock(), body={}),
        ]

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude._execute_tool", return_value="output"):
            with pytest.raises(UsageLimitError) as exc_info:
                invoke_claude("prompt", "/repo")
            # Should carry partial cost from the tokens consumed in the first turn
            assert exc_info.value.cost_eur > 0.0

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
            with pytest.raises(UsageLimitError) as exc_info:
                invoke_claude("prompt", "/repo")
            assert exc_info.value.cost_eur == 0.0

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

        assert result.output == "done"
        assert result.input_tokens == 300  # 100 + 200
        assert result.output_tokens == 150  # 50 + 100
        assert result.cost_eur > 0.0
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


class TestCommitWip:
    def test_commits_and_pushes_changes(self, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            # git checkout branch_name fails (doesn't exist)
            if cmd == ["git", "checkout", "clayde/issue-1"]:
                result.returncode = 1
                return result
            # git diff --cached --quiet returns 1 (has changes)
            if cmd == ["git", "diff", "--cached", "--quiet"]:
                result.returncode = 1
                return result
            result.returncode = 0
            return result

        with patch("clayde.git.subprocess.run", side_effect=fake_run):
            commit_wip("/repo", "clayde/issue-1")

        cmd_strs = [" ".join(c) for c in calls]
        assert any("checkout -b clayde/issue-1" in s for s in cmd_strs)
        assert any("add -A" in s for s in cmd_strs)
        assert any("commit -m" in s for s in cmd_strs)
        assert any("push --force origin clayde/issue-1" in s for s in cmd_strs)

    def test_skips_commit_when_no_changes(self, tmp_path):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0  # git diff --cached --quiet returns 0 (no changes)
            return result

        with patch("clayde.git.subprocess.run", side_effect=fake_run):
            commit_wip("/repo", "clayde/issue-1")

        cmd_strs = [" ".join(c) for c in calls]
        assert not any("commit" in s for s in cmd_strs)

    def test_never_raises(self):
        with patch("clayde.git.subprocess.run", side_effect=OSError("fail")):
            # Should not raise
            commit_wip("/repo", "branch")


class TestConversationPersistence:
    def test_serialize_messages_with_pydantic_blocks(self):
        mock_block = MagicMock()
        mock_block.model_dump.return_value = {"type": "text", "text": "hello"}

        messages = [
            {"role": "user", "content": "prompt"},
            {"role": "assistant", "content": [mock_block]},
        ]
        result = _serialize_messages(messages)
        assert result[0] == {"role": "user", "content": "prompt"}
        assert result[1] == {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}
        mock_block.model_dump.assert_called_once()

    def test_serialize_messages_with_plain_dicts(self):
        messages = [
            {"role": "user", "content": "prompt"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        result = _serialize_messages(messages)
        assert result == messages

    def test_save_and_load_conversation(self, tmp_path):
        conv_path = tmp_path / "conv.json"
        messages = [
            {"role": "user", "content": "prompt"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]
        _save_conversation(conv_path, messages)
        loaded = _load_conversation(conv_path)
        assert loaded == messages

    def test_load_nonexistent_returns_none(self, tmp_path):
        assert _load_conversation(tmp_path / "missing.json") is None

    def test_save_creates_parent_dirs(self, tmp_path):
        conv_path = tmp_path / "sub" / "dir" / "conv.json"
        _save_conversation(conv_path, [{"role": "user", "content": "test"}])
        assert conv_path.exists()

    def test_rate_limit_saves_conversation(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")
        conv_path = tmp_path / "conv.json"

        mock_client = MagicMock()
        mock_client.beta.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limit", response=MagicMock(), body={}
        )

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude.commit_wip") as mock_wip:
            with pytest.raises(UsageLimitError):
                invoke_claude("prompt", "/repo", branch_name="branch", conversation_path=conv_path)

        assert conv_path.exists()
        saved = json.loads(conv_path.read_text())
        assert len(saved) == 1
        assert saved[0]["role"] == "user"
        mock_wip.assert_called_once_with("/repo", "branch")

    def test_rate_limit_529_saves_conversation(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")
        conv_path = tmp_path / "conv.json"

        mock_response_obj = MagicMock()
        mock_response_obj.status_code = 529
        mock_client = MagicMock()
        mock_client.beta.messages.create.side_effect = anthropic.APIStatusError(
            message="overloaded", response=mock_response_obj, body={}
        )

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client), \
             patch("clayde.claude.commit_wip"):
            with pytest.raises(UsageLimitError):
                invoke_claude("prompt", "/repo", branch_name="b", conversation_path=conv_path)

        assert conv_path.exists()

    def test_resumes_from_saved_conversation(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")
        conv_path = tmp_path / "conv.json"

        # Save a prior conversation
        prior_messages = [
            {"role": "user", "content": "original prompt"},
            {"role": "assistant", "content": [{"type": "text", "text": "working on it"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        ]
        conv_path.write_text(json.dumps(prior_messages))

        end_response = _make_end_turn_response("resumed output")
        mock_client = MagicMock()
        mock_client.beta.messages.create.return_value = end_response

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client):
            result = invoke_claude("new prompt", str(tmp_path), conversation_path=conv_path)

        assert result.output == "resumed output"
        # Check the first call's messages (before the loop mutates it)
        first_call = mock_client.beta.messages.create.call_args_list[0]
        messages_sent = first_call.kwargs["messages"]
        # 3 prior + 1 resume + 1 assistant appended by loop = 5, but we check
        # the resume message was at index 3 before the call
        assert any("interrupted" in str(m.get("content", "")).lower() for m in messages_sent if m["role"] == "user")

    def test_no_resume_without_conversation_file(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("identity")
        conv_path = tmp_path / "conv.json"  # Does not exist

        end_response = _make_end_turn_response("fresh output")
        mock_client = MagicMock()
        mock_client.beta.messages.create.return_value = end_response

        with patch("clayde.claude.APP_DIR", tmp_path), \
             patch("clayde.claude.get_settings", return_value=_mock_settings()), \
             patch("clayde.claude._get_client", return_value=mock_client):
            result = invoke_claude("prompt", str(tmp_path), conversation_path=conv_path)

        assert result.output == "fresh output"
        first_call = mock_client.beta.messages.create.call_args_list[0]
        messages_sent = first_call.kwargs["messages"]
        # First message should be the user prompt, second is the assistant response appended by the loop
        assert messages_sent[0]["content"] == "prompt"
        assert messages_sent[0]["role"] == "user"
