"""Tests for clayde.orchestrator."""

from unittest.mock import MagicMock, patch

import pytest

from clayde.orchestrator import (
    _handle_awaiting_approval,
    _handle_interrupted,
    _handle_new_issue,
    main,
)


class TestMain:
    def test_exits_when_disabled(self):
        with patch("clayde.orchestrator.load_config", return_value={"CLAYDE_ENABLED": "false"}):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_returns_when_claude_unavailable(self):
        config = {"CLAYDE_ENABLED": "true", "GITHUB_TOKEN": "tok"}
        with patch("clayde.orchestrator.load_config", return_value=config), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.is_claude_available", return_value=False), \
             patch("clayde.orchestrator.get_github_client") as mock_gc:
            main()
            mock_gc.assert_not_called()

    def test_returns_when_no_assigned_issues(self):
        config = {"CLAYDE_ENABLED": "true", "GITHUB_TOKEN": "tok"}
        with patch("clayde.orchestrator.load_config", return_value=config), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[]), \
             patch("clayde.orchestrator.load_state", return_value={"issues": {}}):
            main()  # Should not raise

    def test_dispatches_new_issue(self):
        config = {"CLAYDE_ENABLED": "true", "GITHUB_TOKEN": "tok"}
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with patch("clayde.orchestrator.load_config", return_value=config), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client") as mock_gc, \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", return_value={"issues": {}}), \
             patch("clayde.orchestrator._handle_new_issue") as mock_handle:
            # Patch _handle_new_issue at module level
            main()
            mock_handle.assert_called_once()

    def test_skips_done_issues(self):
        config = {"CLAYDE_ENABLED": "true", "GITHUB_TOKEN": "tok"}
        issue = MagicMock()
        issue.html_url = "url1"
        state = {"issues": {"url1": {"status": "done"}}}
        with patch("clayde.orchestrator.load_config", return_value=config), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", return_value=state), \
             patch("clayde.orchestrator.plan") as mock_plan:
            main()
            mock_plan.run.assert_not_called()


class TestHandleNewIssue:
    def test_skips_unauthorized(self):
        g = MagicMock()
        issue = MagicMock()
        with patch("clayde.orchestrator.is_issue_authorized", return_value=False), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_new_issue(g, issue, "url")
            mock_plan.run.assert_not_called()

    def test_runs_plan_when_authorized(self):
        g = MagicMock()
        issue = MagicMock()
        with patch("clayde.orchestrator.is_issue_authorized", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_new_issue(g, issue, "url")
            mock_plan.run.assert_called_once_with("url")

    def test_sets_failed_on_exception(self):
        g = MagicMock()
        issue = MagicMock()
        with patch("clayde.orchestrator.is_issue_authorized", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.update_issue_state") as mock_update:
            mock_plan.run.side_effect = RuntimeError("boom")
            _handle_new_issue(g, issue, "url")
            mock_update.assert_called_once_with("url", {"status": "failed"})


class TestHandleAwaitingApproval:
    def test_does_nothing_when_not_approved(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "plan_comment_id": 100}
        with patch("clayde.orchestrator.is_plan_approved", return_value=False), \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_awaiting_approval(g, "url", entry)
            mock_impl.run.assert_not_called()

    def test_runs_implement_when_approved(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "plan_comment_id": 100}
        with patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_awaiting_approval(g, "url", entry)
            mock_impl.run.assert_called_once_with("url")

    def test_sets_failed_on_exception(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "plan_comment_id": 100}
        with patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.implement") as mock_impl, \
             patch("clayde.orchestrator.update_issue_state") as mock_update:
            mock_impl.run.side_effect = RuntimeError("boom")
            _handle_awaiting_approval(g, "url", entry)
            mock_update.assert_called_once_with("url", {"status": "failed"})


class TestHandleInterrupted:
    def test_retries_planning(self):
        entry = {"interrupted_phase": "planning"}
        with patch("clayde.orchestrator.plan") as mock_plan:
            _handle_interrupted("url", entry)
            mock_plan.run.assert_called_once_with("url")

    def test_retries_implementing(self):
        entry = {"interrupted_phase": "implementing"}
        with patch("clayde.orchestrator.implement") as mock_impl:
            _handle_interrupted("url", entry)
            mock_impl.run.assert_called_once_with("url")

    def test_skips_unknown_phase(self):
        entry = {"interrupted_phase": "unknown"}
        with patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_interrupted("url", entry)
            mock_plan.run.assert_not_called()
            mock_impl.run.assert_not_called()

    def test_stays_interrupted_on_error(self):
        entry = {"interrupted_phase": "planning"}
        with patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.update_issue_state") as mock_update:
            mock_plan.run.side_effect = RuntimeError("boom")
            _handle_interrupted("url", entry)
            mock_update.assert_called_once_with("url", {"status": "interrupted"})
