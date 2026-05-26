"""Tests for clayde.tasks.wrap_up."""

from unittest.mock import MagicMock, patch

from clayde.claude import InvocationResult, InvocationTimeoutError, UsageLimitError


def _make_result(output: str, cost_eur: float = 0.10) -> InvocationResult:
    return InvocationResult(output=output, cost_eur=cost_eur, input_tokens=50, output_tokens=25)


def _mock_settings():
    s = MagicMock()
    s.kb_path = "/fake/kb"
    s.ntfy_topic = "testtopic"
    s.ntfy_base_url = "https://ntfy.sh"
    s.ntfy_timeout_s = 5
    return s


def _mock_state():
    return {
        "owner": "o",
        "repo": "r",
        "number": 7,
        "pr_url": "https://github.com/o/r/pull/7",
        "branch_name": "clayde/issue-7",
        "pr_title": "Fix login bug",
    }


class TestRun:
    def test_invokes_claude_with_kb_path(self):
        output = (
            "wrap-up done\n"
            "```json\n"
            '{"title": "Wrapped up o/r#7", "body": "Fixed login bug", "success": true}\n'
            "```"
        )
        with patch("clayde.tasks.wrap_up.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.wrap_up.get_issue_state", return_value=_mock_state()), \
             patch("clayde.tasks.wrap_up.parse_issue_url", return_value=("o", "r", 7)), \
             patch("clayde.tasks.wrap_up.invoke_claude", return_value=_make_result(output)) as mock_claude, \
             patch("clayde.tasks.wrap_up.send_ntfy_sync"):
            from clayde.tasks.wrap_up import run
            run("https://github.com/o/r/issues/7")

        mock_claude.assert_called_once()
        # Second positional arg is repo_path (kb_path)
        assert mock_claude.call_args[0][1] == "/fake/kb"

    def test_sends_ntfy_on_success(self):
        output = (
            "```json\n"
            '{"title": "Wrap done", "body": "Fixed it", "success": true}\n'
            "```"
        )
        with patch("clayde.tasks.wrap_up.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.wrap_up.get_issue_state", return_value=_mock_state()), \
             patch("clayde.tasks.wrap_up.parse_issue_url", return_value=("o", "r", 7)), \
             patch("clayde.tasks.wrap_up.invoke_claude", return_value=_make_result(output)), \
             patch("clayde.tasks.wrap_up.send_ntfy_sync") as mock_notify:
            from clayde.tasks.wrap_up import run
            run("https://github.com/o/r/issues/7")

        mock_notify.assert_called_once()
        call_kw = mock_notify.call_args[1]
        assert call_kw["title"] == "Wrap done"
        assert call_kw["body"] == "Fixed it"
        assert call_kw["success"] is True

    def test_sends_ntfy_on_usage_limit(self):
        with patch("clayde.tasks.wrap_up.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.wrap_up.get_issue_state", return_value=_mock_state()), \
             patch("clayde.tasks.wrap_up.parse_issue_url", return_value=("o", "r", 7)), \
             patch("clayde.tasks.wrap_up.invoke_claude",
                   side_effect=UsageLimitError("limit hit", cost_eur=0.5)), \
             patch("clayde.tasks.wrap_up.send_ntfy_sync") as mock_notify:
            from clayde.tasks.wrap_up import run
            run("https://github.com/o/r/issues/7")

        mock_notify.assert_called_once()
        assert mock_notify.call_args[1]["success"] is False

    def test_sends_ntfy_on_timeout(self):
        with patch("clayde.tasks.wrap_up.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.wrap_up.get_issue_state", return_value=_mock_state()), \
             patch("clayde.tasks.wrap_up.parse_issue_url", return_value=("o", "r", 7)), \
             patch("clayde.tasks.wrap_up.invoke_claude",
                   side_effect=InvocationTimeoutError("timed out", cost_eur=0.0)), \
             patch("clayde.tasks.wrap_up.send_ntfy_sync") as mock_notify:
            from clayde.tasks.wrap_up import run
            run("https://github.com/o/r/issues/7")

        assert mock_notify.call_args[1]["success"] is False

    def test_fallback_on_bad_json(self):
        """Wrap-up still notifies even when Claude outputs no valid JSON."""
        with patch("clayde.tasks.wrap_up.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.wrap_up.get_issue_state", return_value=_mock_state()), \
             patch("clayde.tasks.wrap_up.parse_issue_url", return_value=("o", "r", 7)), \
             patch("clayde.tasks.wrap_up.invoke_claude",
                   return_value=_make_result("done, no json here")), \
             patch("clayde.tasks.wrap_up.send_ntfy_sync") as mock_notify:
            from clayde.tasks.wrap_up import run
            run("https://github.com/o/r/issues/7")

        mock_notify.assert_called_once()
        # Fallback assumes success
        assert mock_notify.call_args[1]["success"] is True

    def test_notify_skipped_when_no_topic(self):
        settings = _mock_settings()
        settings.ntfy_topic = ""
        output = '```json\n{"title":"t","body":"b","success":true}\n```'
        with patch("clayde.tasks.wrap_up.get_settings", return_value=settings), \
             patch("clayde.tasks.wrap_up.get_issue_state", return_value=_mock_state()), \
             patch("clayde.tasks.wrap_up.parse_issue_url", return_value=("o", "r", 7)), \
             patch("clayde.tasks.wrap_up.invoke_claude", return_value=_make_result(output)), \
             patch("clayde.tasks.wrap_up.send_ntfy_sync") as mock_notify:
            from clayde.tasks.wrap_up import run
            run("https://github.com/o/r/issues/7")

        mock_notify.assert_not_called()
