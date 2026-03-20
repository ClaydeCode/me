"""Tests for clayde.orchestrator."""

from unittest.mock import MagicMock, patch

import pytest

from clayde.orchestrator import (
    _handle_awaiting_approval,
    _handle_interrupted,
    _handle_new_issue,
    _handle_pr_open,
    _has_new_comments,
    main,
)


def _mock_settings(enabled=False, github_token="tok", github_username="ClaydeCode"):
    s = MagicMock()
    s.enabled = enabled
    s.github_token = github_token
    s.github_username = github_username
    return s


class TestMain:
    def test_exits_when_disabled(self):
        with patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=False)):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_returns_when_claude_unavailable(self):
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=False), \
             patch("clayde.orchestrator.get_github_client") as mock_gc:
            main()
            mock_gc.assert_not_called()

    def test_returns_when_no_assigned_issues(self):
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[]), \
             patch("clayde.orchestrator.load_state", return_value={"issues": {}}):
            main()

    def test_dispatches_new_issue(self):
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", return_value={"issues": {}}), \
             patch("clayde.orchestrator._handle_new_issue") as mock_handle:
            main()
            mock_handle.assert_called_once()

    def test_skips_done_issues(self):
        issue = MagicMock()
        issue.html_url = "url1"
        state = {"issues": {"url1": {"status": "done"}}}
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", return_value=state), \
             patch("clayde.orchestrator.plan") as mock_plan:
            main()
            mock_plan.run_preliminary.assert_not_called()

    def test_dispatches_awaiting_preliminary(self):
        issue = MagicMock()
        issue.html_url = "url1"
        state = {"issues": {"url1": {
            "status": "awaiting_preliminary_approval",
            "owner": "o", "repo": "r", "number": 1,
            "preliminary_comment_id": 100,
        }}}
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", return_value=state), \
             patch("clayde.orchestrator._handle_awaiting_approval") as mock_handle:
            main()
            mock_handle.assert_called_once()

    def test_dispatches_awaiting_plan(self):
        issue = MagicMock()
        issue.html_url = "url1"
        state = {"issues": {"url1": {
            "status": "awaiting_plan_approval",
            "owner": "o", "repo": "r", "number": 1,
            "plan_comment_id": 200,
        }}}
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", return_value=state), \
             patch("clayde.orchestrator._handle_awaiting_approval") as mock_handle:
            main()
            mock_handle.assert_called_once()

    def test_dispatches_pr_open(self):
        issue = MagicMock()
        issue.html_url = "url1"
        state = {"issues": {"url1": {
            "status": "pr_open",
            "owner": "o", "repo": "r", "number": 1,
            "pr_url": "https://github.com/o/r/pull/5",
        }}}
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", return_value=state), \
             patch("clayde.orchestrator._handle_pr_open") as mock_handle:
            main()
            mock_handle.assert_called_once()

    @pytest.mark.parametrize("transient_status", [
        "preliminary_planning",
        "planning",
        "implementing",
        "addressing_review",
    ])
    def test_recovers_transient_state_to_interrupted(self, transient_status):
        """Issues stuck in transient states are converted to interrupted."""
        issue = MagicMock()
        issue.html_url = "url1"
        state = {"issues": {"url1": {"status": transient_status, "number": 1}}}
        recovered_state = {"issues": {"url1": {
            "status": "interrupted",
            "interrupted_phase": transient_status,
            "number": 1,
        }}}
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", side_effect=[state, recovered_state]), \
             patch("clayde.orchestrator.update_issue_state") as mock_update, \
             patch("clayde.orchestrator._handle_interrupted") as mock_handle:
            main()
            mock_update.assert_called_once_with(
                "url1",
                {"status": "interrupted", "interrupted_phase": transient_status},
            )
            mock_handle.assert_called_once()

    def test_backward_compat_awaiting_approval(self):
        """Old 'awaiting_approval' status maps to 'awaiting_plan_approval'."""
        issue = MagicMock()
        issue.html_url = "url1"
        state = {"issues": {"url1": {
            "status": "awaiting_approval",
            "owner": "o", "repo": "r", "number": 1,
            "plan_comment_id": 200,
        }}}
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.is_claude_available", return_value=True), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[issue]), \
             patch("clayde.orchestrator.load_state", return_value=state), \
             patch("clayde.orchestrator._handle_awaiting_approval") as mock_handle:
            main()
            mock_handle.assert_called_once()


class TestHandleNewIssue:
    def test_skips_blocked_issue(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with patch("clayde.orchestrator.is_blocked", return_value=True), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_new_issue(g, issue, issue.html_url)
            mock_plan.run_preliminary.assert_not_called()

    def test_skips_no_visible_content(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=False), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_new_issue(g, issue, issue.html_url)
            mock_plan.run_preliminary.assert_not_called()

    def test_runs_preliminary_plan_when_visible(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_new_issue(g, issue, issue.html_url)
            mock_plan.run_preliminary.assert_called_once_with(issue.html_url)

    def test_sets_failed_on_exception(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.update_issue_state") as mock_update:
            mock_plan.run_preliminary.side_effect = RuntimeError("boom")
            _handle_new_issue(g, issue, issue.html_url)
            mock_update.assert_called_once_with(issue.html_url, {"status": "failed"})

    def test_proceeds_if_blocked_check_fails(self):
        """If blocked-check raises, proceed anyway (fail open)."""
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with patch("clayde.orchestrator.is_blocked", side_effect=Exception("API error")), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_new_issue(g, issue, issue.html_url)
            mock_plan.run_preliminary.assert_called_once()


class TestHandleAwaitingPreliminary:
    def test_does_nothing_when_not_approved_and_no_new_comments(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "preliminary_comment_id": 100}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=False), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_awaiting_approval(g, "url", entry, phase="preliminary")
            mock_plan.run_thorough.assert_not_called()
            mock_plan.run_update.assert_not_called()

    def test_runs_thorough_when_approved(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "preliminary_comment_id": 100}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_awaiting_approval(g, "url", entry, phase="preliminary")
            mock_plan.run_thorough.assert_called_once_with("url")

    def test_runs_update_when_new_comments(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "preliminary_comment_id": 100}
        with patch("clayde.orchestrator._has_new_comments", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_awaiting_approval(g, "url", entry, phase="preliminary")
            mock_plan.run_update.assert_called_once_with("url", "preliminary")

    def test_runs_implement_directly_when_small_and_approved(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "preliminary_comment_id": 100, "size": "small"}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_awaiting_approval(g, "url", entry, phase="preliminary")
            mock_plan.run_thorough.assert_not_called()
            mock_impl.run.assert_called_once_with("url")

    def test_runs_thorough_when_large_and_approved(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "preliminary_comment_id": 100, "size": "large"}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_awaiting_approval(g, "url", entry, phase="preliminary")
            mock_plan.run_thorough.assert_called_once_with("url")
            mock_impl.run.assert_not_called()

    def test_defaults_to_large_when_size_absent(self):
        """In-flight issues without size in state default to large behavior."""
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "preliminary_comment_id": 100}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_awaiting_approval(g, "url", entry, phase="preliminary")
            mock_plan.run_thorough.assert_called_once_with("url")
            mock_impl.run.assert_not_called()

    def test_sets_failed_on_thorough_exception(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "preliminary_comment_id": 100}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.update_issue_state") as mock_update:
            mock_plan.run_thorough.side_effect = RuntimeError("boom")
            _handle_awaiting_approval(g, "url", entry, phase="preliminary")
            mock_update.assert_called_once_with("url", {"status": "failed"})

    def test_marks_failed_if_no_preliminary_comment_id(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1}
        with patch("clayde.orchestrator.update_issue_state") as mock_update:
            _handle_awaiting_approval(g, "url", entry, phase="preliminary")
            mock_update.assert_called_once_with("url", {"status": "failed"})


class TestHandleAwaitingPlan:
    def test_does_nothing_when_not_approved_and_no_new_comments(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "plan_comment_id": 200}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=False), \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_awaiting_approval(g, "url", entry, phase="thorough")
            mock_impl.run.assert_not_called()

    def test_runs_implement_when_approved(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "plan_comment_id": 200}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_awaiting_approval(g, "url", entry, phase="thorough")
            mock_impl.run.assert_called_once_with("url")

    def test_runs_update_when_new_comments(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "plan_comment_id": 200}
        with patch("clayde.orchestrator._has_new_comments", return_value=True), \
             patch("clayde.orchestrator.plan") as mock_plan:
            _handle_awaiting_approval(g, "url", entry, phase="thorough")
            mock_plan.run_update.assert_called_once_with("url", "thorough")

    def test_sets_failed_on_exception(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1, "plan_comment_id": 200}
        with patch("clayde.orchestrator._has_new_comments", return_value=False), \
             patch("clayde.orchestrator.is_plan_approved", return_value=True), \
             patch("clayde.orchestrator.implement") as mock_impl, \
             patch("clayde.orchestrator.update_issue_state") as mock_update:
            mock_impl.run.side_effect = RuntimeError("boom")
            _handle_awaiting_approval(g, "url", entry, phase="thorough")
            mock_update.assert_called_once_with("url", {"status": "failed"})

    def test_marks_failed_if_no_plan_comment_id(self):
        g = MagicMock()
        entry = {"owner": "o", "repo": "r", "number": 1}
        with patch("clayde.orchestrator.update_issue_state") as mock_update:
            _handle_awaiting_approval(g, "url", entry, phase="thorough")
            mock_update.assert_called_once_with("url", {"status": "failed"})


class TestHandlePrOpen:
    def test_runs_review(self):
        g = MagicMock()
        entry = {"number": 1, "pr_url": "https://github.com/o/r/pull/5"}
        with patch("clayde.orchestrator.review") as mock_review:
            _handle_pr_open(g, "url", entry)
            mock_review.run.assert_called_once_with("url")

    def test_sets_failed_on_exception(self):
        g = MagicMock()
        entry = {"number": 1}
        with patch("clayde.orchestrator.review") as mock_review, \
             patch("clayde.orchestrator.update_issue_state") as mock_update:
            mock_review.run.side_effect = RuntimeError("boom")
            _handle_pr_open(g, "url", entry)
            mock_update.assert_called_once_with("url", {"status": "failed"})


class TestHandleInterrupted:
    def test_retries_preliminary_planning(self):
        entry = {"interrupted_phase": "preliminary_planning"}
        with patch("clayde.orchestrator.plan") as mock_plan:
            _handle_interrupted("url", entry)
            mock_plan.run_preliminary.assert_called_once_with("url")

    def test_retries_planning(self):
        entry = {"interrupted_phase": "planning"}
        with patch("clayde.orchestrator.plan") as mock_plan:
            _handle_interrupted("url", entry)
            mock_plan.run_thorough.assert_called_once_with("url")

    def test_retries_implementing(self):
        entry = {"interrupted_phase": "implementing"}
        with patch("clayde.orchestrator.implement") as mock_impl:
            _handle_interrupted("url", entry)
            mock_impl.run.assert_called_once_with("url")

    def test_retries_addressing_review(self):
        entry = {"interrupted_phase": "addressing_review"}
        with patch("clayde.orchestrator.review") as mock_review:
            _handle_interrupted("url", entry)
            mock_review.run.assert_called_once_with("url")

    def test_skips_unknown_phase(self):
        entry = {"interrupted_phase": "unknown"}
        with patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.implement") as mock_impl:
            _handle_interrupted("url", entry)
            mock_plan.run_preliminary.assert_not_called()
            mock_plan.run_thorough.assert_not_called()
            mock_impl.run.assert_not_called()

    def test_stays_interrupted_on_error(self):
        entry = {"interrupted_phase": "preliminary_planning"}
        with patch("clayde.orchestrator.plan") as mock_plan, \
             patch("clayde.orchestrator.update_issue_state") as mock_update:
            mock_plan.run_preliminary.side_effect = RuntimeError("boom")
            _handle_interrupted("url", entry)
            mock_update.assert_called_once_with("url", {"status": "interrupted"})


class TestHasNewComments:
    def test_detects_new_visible_comments(self):
        g = MagicMock()
        comment = MagicMock()
        with patch("clayde.orchestrator.fetch_issue_comments", return_value=[comment]), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[comment]):
            entry = {"last_seen_comment_id": 100}
            assert _has_new_comments(g, "o", "r", 1, entry) is True

    def test_no_new_comments(self):
        g = MagicMock()
        comment = MagicMock()
        with patch("clayde.orchestrator.fetch_issue_comments", return_value=[comment]), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[]):
            entry = {"last_seen_comment_id": 100}
            assert _has_new_comments(g, "o", "r", 1, entry) is False
