"""Tests for clayde.orchestrator — event-driven loop."""

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from clayde.claude import ClaudeStatus
from clayde.orchestrator import (
    _handle_issue,
    _handle_standalone_pr,
    _is_pr_tracked_as_issue,
    _now_utc,
    _parse_timestamp,
    _prune_closed_issues,
    main,
)


def _mock_settings(enabled=False, github_token="tok", github_username="ClaydeCode"):
    s = MagicMock()
    s.enabled = enabled
    s.github_token = github_token
    s.github_username = github_username
    s.effective_git_name = "Test Bot"
    s.git_email = "test@example.com"
    return s


@contextmanager
def _patched_main(enabled=True, claude_status=ClaudeStatus.AVAILABLE, assigned=(), state=None):
    """Patch every external dependency `main()` touches.

    Yields a dict of name → mock so individual tests can assert on call behavior.
    """
    targets = {
        "get_settings": {"return_value": _mock_settings(enabled=enabled)},
        "setup_logging": {},
        "init_tracer": {},
        "_configure_global_git_identity": {},
        "check_claude_availability": {"return_value": claude_status},
        "get_claude_auth_notified": {"return_value": False},
        "set_claude_auth_notified": {},
        "send_ntfy_sync": {},
        "get_github_client": {},
        "get_assigned_issues": {"return_value": list(assigned)},
        "load_state": {"return_value": state if state is not None else {"issues": {}}},
        "_prune_closed_issues": {},
        "_handle_issue": {},
    }
    with ExitStack() as stack:
        yield {
            name: stack.enter_context(patch(f"clayde.orchestrator.{name}", **kwargs))
            for name, kwargs in targets.items()
        }


class TestMain:
    def test_exits_when_disabled(self):
        with _patched_main(enabled=False):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_returns_when_claude_usage_limited(self):
        with _patched_main(claude_status=ClaudeStatus.USAGE_LIMIT) as mocks:
            main()
            mocks["get_github_client"].assert_not_called()
            mocks["send_ntfy_sync"].assert_not_called()

    def test_returns_when_no_assigned_issues(self):
        with _patched_main(assigned=[]):
            main()

    def test_calls_handle_issue_for_each_assigned(self):
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with _patched_main(assigned=[issue]) as mocks:
            main()
            mocks["_handle_issue"].assert_called_once()

    def test_main_calls_prune(self):
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        with _patched_main(assigned=[issue]) as mocks:
            main()
            mocks["_prune_closed_issues"].assert_called_once()


class TestClaudeAuthFailureNotification:
    def test_notifies_on_auth_failure(self):
        with _patched_main(claude_status=ClaudeStatus.AUTH_FAILED) as mocks:
            main()
            mocks["send_ntfy_sync"].assert_called_once()
            kwargs = mocks["send_ntfy_sync"].call_args.kwargs
            assert kwargs["title"] == "Clayde: Claude CLI auth failed"
            assert kwargs["success"] is False
            mocks["set_claude_auth_notified"].assert_called_once_with(True)
            # No work is attempted on auth failure
            mocks["get_github_client"].assert_not_called()

    def test_does_not_renotify_when_already_alerted(self):
        with _patched_main(claude_status=ClaudeStatus.AUTH_FAILED) as mocks:
            mocks["get_claude_auth_notified"].return_value = True
            main()
            mocks["send_ntfy_sync"].assert_not_called()
            mocks["set_claude_auth_notified"].assert_not_called()
            mocks["get_github_client"].assert_not_called()

    def test_no_notification_on_usage_limit(self):
        with _patched_main(claude_status=ClaudeStatus.USAGE_LIMIT) as mocks:
            main()
            mocks["send_ntfy_sync"].assert_not_called()

    def test_clears_latch_when_available_again(self):
        with _patched_main(claude_status=ClaudeStatus.AVAILABLE, assigned=[]) as mocks:
            mocks["get_claude_auth_notified"].return_value = True
            main()
            mocks["set_claude_auth_notified"].assert_called_once_with(False)

    def test_does_not_clear_latch_when_never_set(self):
        with _patched_main(claude_status=ClaudeStatus.AVAILABLE, assigned=[]) as mocks:
            main()
            mocks["set_claude_auth_notified"].assert_not_called()


class TestHandleIssue:
    def test_skips_blocked_issue(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        with patch("clayde.orchestrator.is_blocked", return_value=True), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)
            mock_work.run.assert_not_called()

    def test_skips_no_visible_content(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=False), \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)
            mock_work.run.assert_not_called()

    def test_invokes_work_on_first_time(self):
        """No last_seen_at means first-time evaluation — always invoke."""
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state", return_value={}), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[]), \
             patch("clayde.orchestrator.update_issue_state"), \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)
            mock_work.run.assert_called_once_with(issue.html_url)

    def test_invokes_work_on_new_comments(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        new_comment = MagicMock()
        ts = "2024-01-01T12:00:00+00:00"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[new_comment]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state", return_value={"last_seen_at": ts}), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[new_comment]), \
             patch("clayde.orchestrator.update_issue_state"), \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)
            mock_work.run.assert_called_once_with(issue.html_url)

    def test_skips_when_no_new_activity(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        ts = "2024-01-01T12:00:00+00:00"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state", return_value={"last_seen_at": ts}), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[]), \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)
            mock_work.run.assert_not_called()

    def test_invokes_work_when_in_progress(self):
        """Crash recovery: in_progress=True triggers invocation even with no new activity."""
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        ts = "2024-01-01T12:00:00+00:00"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state",
                   return_value={"last_seen_at": ts, "in_progress": True}), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[]), \
             patch("clayde.orchestrator.update_issue_state"), \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)
            mock_work.run.assert_called_once_with(issue.html_url)

    def test_sets_in_progress_before_invoke_and_clears_after(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        calls = []
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state", return_value={}), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[]), \
             patch("clayde.orchestrator.update_issue_state",
                   side_effect=lambda url, upd: calls.append(upd)), \
             patch("clayde.orchestrator.work"):
            _handle_issue(g, issue, issue.html_url)

        # First update sets in_progress=True
        assert calls[0] == {"in_progress": True}
        # Last update clears in_progress and sets last_seen_at
        last = calls[-1]
        assert last.get("in_progress") is False
        assert "last_seen_at" in last

    def test_leaves_in_progress_on_usage_limit(self):
        from clayde.claude import UsageLimitError
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        calls = []
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state", return_value={}), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[]), \
             patch("clayde.orchestrator.update_issue_state",
                   side_effect=lambda url, upd: calls.append(upd)), \
             patch("clayde.orchestrator.work") as mock_work:
            mock_work.run.side_effect = UsageLimitError("limit", cost_eur=0.0)
            _handle_issue(g, issue, issue.html_url)

        # First call sets in_progress=True, nothing clears it
        assert calls[0] == {"in_progress": True}
        # No subsequent call should set in_progress=False
        assert not any(c.get("in_progress") is False for c in calls)

    def test_proceeds_if_blocked_check_fails(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        with patch("clayde.orchestrator.is_blocked", side_effect=Exception("API error")), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state", return_value={}), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[]), \
             patch("clayde.orchestrator.update_issue_state"), \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)
            mock_work.run.assert_called_once()

    def test_clears_in_progress_on_unexpected_error(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Test"
        calls = []
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state", return_value={}), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[]), \
             patch("clayde.orchestrator.update_issue_state",
                   side_effect=lambda url, upd: calls.append(upd)), \
             patch("clayde.orchestrator.work") as mock_work:
            mock_work.run.side_effect = RuntimeError("boom")
            _handle_issue(g, issue, issue.html_url)

        # in_progress should be cleared to False after an unexpected error
        assert any(c.get("in_progress") is False for c in calls)


class TestParseTimestamp:
    def test_parses_utc_z_suffix(self):
        ts = "2024-01-15T10:30:00Z"
        result = _parse_timestamp(ts)
        assert result is not None
        assert result.tzinfo is not None
        assert result.year == 2024

    def test_parses_offset_format(self):
        ts = "2024-01-15T10:30:00+00:00"
        result = _parse_timestamp(ts)
        assert result is not None

    def test_returns_none_for_none(self):
        assert _parse_timestamp(None) is None

    def test_returns_none_for_empty(self):
        assert _parse_timestamp("") is None

    def test_returns_none_for_invalid(self):
        assert _parse_timestamp("not-a-timestamp") is None


class TestNowUtc:
    def test_returns_iso_string(self):
        ts = _now_utc()
        assert isinstance(ts, str)
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None


class TestPruneClosedIssues:
    def _make_issue_state(self, owner="o", repo="r", number=1):
        return {"owner": owner, "repo": repo, "number": number}

    def test_prunes_closed_issue(self):
        g = MagicMock()
        gh_issue = MagicMock()
        gh_issue.state = "closed"
        issues_state = {"https://github.com/o/r/issues/1": self._make_issue_state()}
        with patch("clayde.orchestrator.fetch_issue", return_value=gh_issue), \
             patch("clayde.orchestrator.load_state", return_value={"issues": dict(issues_state)}), \
             patch("clayde.orchestrator.save_state") as mock_save:
            _prune_closed_issues(g, issues_state)
            saved = mock_save.call_args[0][0]
            assert "https://github.com/o/r/issues/1" not in saved["issues"]

    def test_keeps_open_issue(self):
        g = MagicMock()
        gh_issue = MagicMock()
        gh_issue.state = "open"
        issues_state = {"https://github.com/o/r/issues/1": self._make_issue_state()}
        with patch("clayde.orchestrator.fetch_issue", return_value=gh_issue), \
             patch("clayde.orchestrator.save_state") as mock_save:
            _prune_closed_issues(g, issues_state)
            mock_save.assert_not_called()

    def test_skips_entry_missing_fields(self):
        g = MagicMock()
        issues_state = {"url1": {"last_seen_at": "2024-01-01T00:00:00Z"}}
        with patch("clayde.orchestrator.fetch_issue") as mock_fetch, \
             patch("clayde.orchestrator.save_state") as mock_save:
            _prune_closed_issues(g, issues_state)
            mock_fetch.assert_not_called()
            mock_save.assert_not_called()

    def test_skips_on_api_error(self):
        g = MagicMock()
        issues_state = {"https://github.com/o/r/issues/1": self._make_issue_state()}
        with patch("clayde.orchestrator.fetch_issue", side_effect=Exception("API error")), \
             patch("clayde.orchestrator.save_state") as mock_save:
            _prune_closed_issues(g, issues_state)
            mock_save.assert_not_called()

    def test_prunes_multiple_closed_in_one_save(self):
        g = MagicMock()
        gh_issue = MagicMock()
        gh_issue.state = "closed"
        url1 = "https://github.com/o/r/issues/1"
        url2 = "https://github.com/o/r/issues/2"
        issues_state = {
            url1: self._make_issue_state(number=1),
            url2: self._make_issue_state(number=2),
        }
        with patch("clayde.orchestrator.fetch_issue", return_value=gh_issue), \
             patch("clayde.orchestrator.load_state", return_value={"issues": dict(issues_state)}), \
             patch("clayde.orchestrator.save_state") as mock_save:
            _prune_closed_issues(g, issues_state)
            assert mock_save.call_count == 1
            saved = mock_save.call_args[0][0]
            assert url1 not in saved["issues"]
            assert url2 not in saved["issues"]


class TestIsPrTrackedAsIssue:
    def test_returns_true_when_pr_url_matches_issue_state(self):
        issues_state = {
            "https://github.com/o/r/issues/1": {
                "pr_url": "https://github.com/o/r/pull/5"
            }
        }
        assert _is_pr_tracked_as_issue("https://github.com/o/r/pull/5", issues_state)

    def test_returns_false_when_no_match(self):
        issues_state = {
            "https://github.com/o/r/issues/1": {
                "pr_url": "https://github.com/o/r/pull/5"
            }
        }
        assert not _is_pr_tracked_as_issue("https://github.com/o/r/pull/99", issues_state)

    def test_returns_false_for_empty_state(self):
        assert not _is_pr_tracked_as_issue("https://github.com/o/r/pull/5", {})

    def test_returns_false_when_state_has_no_pr_url(self):
        issues_state = {
            "https://github.com/o/r/issues/1": {"owner": "o", "repo": "r", "number": 1}
        }
        assert not _is_pr_tracked_as_issue("https://github.com/o/r/pull/5", issues_state)


class TestMainDispatch:
    """Tests that main() correctly dispatches issues vs standalone PRs."""

    def test_dispatches_issue_to_handle_issue(self):
        item = MagicMock()
        item.html_url = "https://github.com/o/r/issues/1"
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.check_claude_availability", return_value=ClaudeStatus.AVAILABLE), \
             patch("clayde.orchestrator.get_claude_auth_notified", return_value=False), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[item]), \
             patch("clayde.orchestrator.load_state", return_value={"issues": {}}), \
             patch("clayde.orchestrator._prune_closed_issues"), \
             patch("clayde.orchestrator.is_pull_request_item", return_value=False), \
             patch("clayde.orchestrator._handle_issue") as mock_issue, \
             patch("clayde.orchestrator._handle_standalone_pr") as mock_pr:
            main()
        mock_issue.assert_called_once()
        mock_pr.assert_not_called()

    def test_dispatches_standalone_pr_to_handle_standalone_pr(self):
        item = MagicMock()
        item.html_url = "https://github.com/o/r/pull/80"
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.check_claude_availability", return_value=ClaudeStatus.AVAILABLE), \
             patch("clayde.orchestrator.get_claude_auth_notified", return_value=False), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[item]), \
             patch("clayde.orchestrator.load_state", return_value={"issues": {}}), \
             patch("clayde.orchestrator._prune_closed_issues"), \
             patch("clayde.orchestrator.is_pull_request_item", return_value=True), \
             patch("clayde.orchestrator._is_pr_tracked_as_issue", return_value=False), \
             patch("clayde.orchestrator._handle_issue") as mock_issue, \
             patch("clayde.orchestrator._handle_standalone_pr") as mock_pr:
            main()
        mock_issue.assert_not_called()
        mock_pr.assert_called_once()

    def test_skips_pr_already_tracked_as_issue(self):
        item = MagicMock()
        item.html_url = "https://github.com/o/r/pull/5"
        with patch("clayde.orchestrator.get_settings", return_value=_mock_settings(enabled=True)), \
             patch("clayde.orchestrator.setup_logging"), \
             patch("clayde.orchestrator.init_tracer"), \
             patch("clayde.orchestrator.check_claude_availability", return_value=ClaudeStatus.AVAILABLE), \
             patch("clayde.orchestrator.get_claude_auth_notified", return_value=False), \
             patch("clayde.orchestrator.get_github_client"), \
             patch("clayde.orchestrator.get_assigned_issues", return_value=[item]), \
             patch("clayde.orchestrator.load_state", return_value={"issues": {}}), \
             patch("clayde.orchestrator._prune_closed_issues"), \
             patch("clayde.orchestrator.is_pull_request_item", return_value=True), \
             patch("clayde.orchestrator._is_pr_tracked_as_issue", return_value=True), \
             patch("clayde.orchestrator._handle_issue") as mock_issue, \
             patch("clayde.orchestrator._handle_standalone_pr") as mock_pr:
            main()
        mock_issue.assert_not_called()
        mock_pr.assert_not_called()


class TestHandleStandalonePr:
    PR_URL = "https://github.com/o/r/pull/80"

    def _mock_settings(self, username="ClaydeCode"):
        s = MagicMock()
        s.github_username = username
        return s

    def test_skips_when_no_review_activity(self):
        ts = "2024-01-01T12:00:00+00:00"
        with patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.orchestrator.get_issue_state", return_value={"last_seen_at": ts}), \
             patch("clayde.orchestrator.get_pr_reviews", return_value=[]), \
             patch("clayde.orchestrator.get_pr_review_comments", return_value=[]), \
             patch("clayde.orchestrator.filter_pr_reviews", return_value=[]), \
             patch("clayde.orchestrator.get_settings", return_value=self._mock_settings()), \
             patch("clayde.orchestrator.pr_work") as mock_pr_work:
            _handle_standalone_pr(MagicMock(), self.PR_URL)
        mock_pr_work.run.assert_not_called()

    def test_invokes_pr_work_on_new_review(self):
        ts = "2024-01-01T12:00:00+00:00"
        review = MagicMock()
        review.submitted_at = datetime(2024, 2, 1, tzinfo=timezone.utc)
        review.body = "Please fix this"
        review.id = 1
        with patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.orchestrator.get_issue_state", return_value={"last_seen_at": ts}), \
             patch("clayde.orchestrator.get_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_pr_review_comments", return_value=[]), \
             patch("clayde.orchestrator.filter_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_settings", return_value=self._mock_settings()), \
             patch("clayde.orchestrator.update_issue_state"), \
             patch("clayde.orchestrator.pr_work") as mock_pr_work:
            _handle_standalone_pr(MagicMock(), self.PR_URL)
        mock_pr_work.run.assert_called_once_with(self.PR_URL)

    def test_invokes_on_first_time_with_reviews(self):
        """No last_seen_at + reviews present → invoke."""
        review = MagicMock()
        review.body = "Add tests"
        review.id = 42
        with patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.orchestrator.get_issue_state", return_value={}), \
             patch("clayde.orchestrator.get_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_pr_review_comments", return_value=[]), \
             patch("clayde.orchestrator.filter_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_settings", return_value=self._mock_settings()), \
             patch("clayde.orchestrator.update_issue_state"), \
             patch("clayde.orchestrator.pr_work") as mock_pr_work:
            _handle_standalone_pr(MagicMock(), self.PR_URL)
        mock_pr_work.run.assert_called_once_with(self.PR_URL)

    def test_skips_on_first_time_with_no_reviews(self):
        """No last_seen_at + no reviews → skip (nothing to address yet)."""
        with patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.orchestrator.get_issue_state", return_value={}), \
             patch("clayde.orchestrator.get_pr_reviews", return_value=[]), \
             patch("clayde.orchestrator.get_pr_review_comments", return_value=[]), \
             patch("clayde.orchestrator.filter_pr_reviews", return_value=[]), \
             patch("clayde.orchestrator.get_settings", return_value=self._mock_settings()), \
             patch("clayde.orchestrator.pr_work") as mock_pr_work:
            _handle_standalone_pr(MagicMock(), self.PR_URL)
        mock_pr_work.run.assert_not_called()

    def test_retries_when_in_progress(self):
        """in_progress=True → always invoke (crash recovery)."""
        ts = "2024-01-01T12:00:00+00:00"
        with patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.orchestrator.get_issue_state",
                   return_value={"last_seen_at": ts, "in_progress": True}), \
             patch("clayde.orchestrator.get_pr_reviews", return_value=[]), \
             patch("clayde.orchestrator.get_pr_review_comments", return_value=[]), \
             patch("clayde.orchestrator.filter_pr_reviews", return_value=[]), \
             patch("clayde.orchestrator.get_settings", return_value=self._mock_settings()), \
             patch("clayde.orchestrator.update_issue_state"), \
             patch("clayde.orchestrator.pr_work") as mock_pr_work:
            _handle_standalone_pr(MagicMock(), self.PR_URL)
        mock_pr_work.run.assert_called_once_with(self.PR_URL)

    def test_pure_approval_updates_timestamp_only(self):
        """Review with no body/comments but approval state → update last_seen_at, no Claude."""
        ts = "2024-01-01T12:00:00+00:00"
        review = MagicMock()
        review.submitted_at = datetime(2024, 2, 1, tzinfo=timezone.utc)
        review.body = ""
        review.id = 1
        calls = []
        with patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.orchestrator.get_issue_state", return_value={"last_seen_at": ts}), \
             patch("clayde.orchestrator.get_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_pr_review_comments", return_value=[]), \
             patch("clayde.orchestrator.filter_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_settings", return_value=self._mock_settings()), \
             patch("clayde.orchestrator.update_issue_state",
                   side_effect=lambda url, upd: calls.append(upd)), \
             patch("clayde.orchestrator.pr_work") as mock_pr_work:
            _handle_standalone_pr(MagicMock(), self.PR_URL)
        mock_pr_work.run.assert_not_called()
        assert any("last_seen_at" in c for c in calls)

    def test_clears_in_progress_on_unexpected_error(self):
        ts = "2024-01-01T12:00:00+00:00"
        review = MagicMock()
        review.submitted_at = datetime(2024, 2, 1, tzinfo=timezone.utc)
        review.body = "Fix it"
        review.id = 1
        calls = []
        with patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.orchestrator.get_issue_state", return_value={"last_seen_at": ts}), \
             patch("clayde.orchestrator.get_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_pr_review_comments", return_value=[]), \
             patch("clayde.orchestrator.filter_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_settings", return_value=self._mock_settings()), \
             patch("clayde.orchestrator.update_issue_state",
                   side_effect=lambda url, upd: calls.append(upd)), \
             patch("clayde.orchestrator.pr_work") as mock_pr_work:
            mock_pr_work.run.side_effect = RuntimeError("boom")
            _handle_standalone_pr(MagicMock(), self.PR_URL)
        assert any(c.get("in_progress") is False for c in calls)

    def test_leaves_in_progress_on_usage_limit(self):
        from clayde.claude import UsageLimitError
        ts = "2024-01-01T12:00:00+00:00"
        review = MagicMock()
        review.submitted_at = datetime(2024, 2, 1, tzinfo=timezone.utc)
        review.body = "Fix it"
        review.id = 1
        calls = []
        with patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.orchestrator.get_issue_state", return_value={"last_seen_at": ts}), \
             patch("clayde.orchestrator.get_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_pr_review_comments", return_value=[]), \
             patch("clayde.orchestrator.filter_pr_reviews", return_value=[review]), \
             patch("clayde.orchestrator.get_settings", return_value=self._mock_settings()), \
             patch("clayde.orchestrator.update_issue_state",
                   side_effect=lambda url, upd: calls.append(upd)), \
             patch("clayde.orchestrator.pr_work") as mock_pr_work:
            mock_pr_work.run.side_effect = UsageLimitError("limit", cost_eur=0.0)
            _handle_standalone_pr(MagicMock(), self.PR_URL)
        # Should NOT clear in_progress
        assert not any(c.get("in_progress") is False for c in calls)


def test_run_loop_without_pebble_uses_legacy_path(monkeypatch):
    """When pebble_enabled is False, run_loop must use the existing sync path."""
    from clayde import orchestrator

    calls = []
    monkeypatch.setattr(orchestrator, "main", lambda: calls.append("tick"))
    monkeypatch.setattr(orchestrator, "_shutdown", True)
    monkeypatch.setattr(orchestrator, "setup_logging", lambda: None)

    class _S:
        loop_interval_s = 0
        pebble_enabled = False

    monkeypatch.setattr(orchestrator, "get_settings", lambda: _S())
    orchestrator.run_loop()


def test_run_loop_with_pebble_invokes_async_entry(monkeypatch):
    """When pebble_enabled is True, run_loop must hand off to the async entry."""
    from clayde import orchestrator

    invoked = {}

    async def fake_async_main():
        invoked["called"] = True

    monkeypatch.setattr(orchestrator, "_run_with_pebble", fake_async_main)

    class _S:
        loop_interval_s = 0
        pebble_enabled = True
        pebble_token = "x"
        pebble_port = 8080
        pebble_timeout = 10
        pebble_queue_max = 2

    monkeypatch.setattr(orchestrator, "get_settings", lambda: _S())
    orchestrator.run_loop()
    assert invoked.get("called") is True


class TestMergeDetection:
    """Tests for PR merge detection and wrap-up invocation."""

    def _base_patches(self, pr_url="https://github.com/o/r/pull/5", merged=True, already_wrapped=False):
        """Return a list of common patch targets for merge-detection tests."""
        return [
            patch("clayde.orchestrator.is_blocked", return_value=False),
            patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)),
            patch("clayde.orchestrator.fetch_issue_comments", return_value=[]),
            patch("clayde.orchestrator.has_visible_content", return_value=True),
            patch("clayde.orchestrator.get_issue_state", return_value={
                "pr_url": pr_url,
                "last_seen_at": "2024-01-01T12:00:00+00:00",
                "merged": already_wrapped,
            }),
            patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 5)),
            patch("clayde.orchestrator.get_pull", return_value=MagicMock(merged=merged)),
            patch("clayde.orchestrator.update_issue_state"),
            patch("clayde.orchestrator.get_new_visible_comments", return_value=[]),
        ]

    def test_merged_pr_runs_wrap_up_once(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Fix bug"
        patches = self._base_patches(merged=True, already_wrapped=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], \
             patch("clayde.orchestrator.wrap_up") as mock_wrap_up, \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)

        mock_wrap_up.run.assert_called_once_with(issue.html_url)
        mock_work.run.assert_not_called()

    def test_merged_pr_sets_merged_flag(self):
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Fix bug"
        patches = self._base_patches(merged=True, already_wrapped=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7] as mock_update, patches[8], \
             patch("clayde.orchestrator.wrap_up"), \
             patch("clayde.orchestrator.work"):
            _handle_issue(g, issue, issue.html_url)

        assert any(c[0][1].get("merged") is True for c in mock_update.call_args_list)

    def test_already_wrapped_up_skips_wrap_up(self):
        """Second cycle after merge: merged flag set → skip wrap-up, just return."""
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Fix bug"
        patches = self._base_patches(merged=True, already_wrapped=True)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], \
             patch("clayde.orchestrator.wrap_up") as mock_wrap_up, \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)

        mock_wrap_up.run.assert_not_called()
        mock_work.run.assert_not_called()

    def test_wrap_up_failure_does_not_propagate(self):
        """Wrap-up errors are caught — _handle_issue must not raise."""
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Fix bug"
        patches = self._base_patches(merged=True, already_wrapped=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], \
             patch("clayde.orchestrator.wrap_up") as mock_wrap_up, \
             patch("clayde.orchestrator.work"):
            mock_wrap_up.run.side_effect = Exception("wrap-up exploded")
            _handle_issue(g, issue, issue.html_url)  # must not raise

    def test_unmerged_pr_falls_through_to_work(self):
        """PR exists but not merged → normal work invocation."""
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Fix bug"
        patches = self._base_patches(merged=False, already_wrapped=False)
        with patches[0], patches[1], patches[2], patches[3], \
             patch("clayde.orchestrator.get_issue_state", return_value={
                 "pr_url": "https://github.com/o/r/pull/5",
             }), \
             patches[5], patches[6], patches[7], \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[MagicMock()]), \
             patch("clayde.orchestrator.wrap_up") as mock_wrap_up, \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)

        mock_wrap_up.run.assert_not_called()
        mock_work.run.assert_called_once_with(issue.html_url)

    def test_get_pull_failure_falls_through(self):
        """get_pull error → logged and falls through to normal work."""
        g = MagicMock()
        issue = MagicMock()
        issue.html_url = "https://github.com/o/r/issues/1"
        issue.title = "Fix bug"
        with patch("clayde.orchestrator.is_blocked", return_value=False), \
             patch("clayde.orchestrator.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.orchestrator.fetch_issue_comments", return_value=[]), \
             patch("clayde.orchestrator.has_visible_content", return_value=True), \
             patch("clayde.orchestrator.get_issue_state", return_value={
                 "pr_url": "https://github.com/o/r/pull/5",
             }), \
             patch("clayde.orchestrator.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.orchestrator.get_pull", side_effect=Exception("API error")), \
             patch("clayde.orchestrator.update_issue_state"), \
             patch("clayde.orchestrator.get_new_visible_comments", return_value=[MagicMock()]), \
             patch("clayde.orchestrator.wrap_up") as mock_wrap_up, \
             patch("clayde.orchestrator.work") as mock_work:
            _handle_issue(g, issue, issue.html_url)

        mock_wrap_up.run.assert_not_called()
        mock_work.run.assert_called_once_with(issue.html_url)
