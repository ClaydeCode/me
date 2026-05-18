"""Tests for clayde.tasks.work — unified work task."""

from unittest.mock import MagicMock, patch

from clayde.claude import InvocationResult, InvocationTimeoutError, UsageLimitError
from clayde.tasks.work import _assign_reviewer, _format_reviews, run


def _make_result(output: str, cost_eur: float = 0.50) -> InvocationResult:
    return InvocationResult(output=output, cost_eur=cost_eur, input_tokens=100, output_tokens=50)


def _mock_settings(github_username="ClaydeCode"):
    s = MagicMock()
    s.github_username = github_username
    return s


class TestRun:
    def _base_patches(self):
        """Common patches for run() tests."""
        mock_issue = MagicMock()
        mock_issue.title = "Test issue"
        mock_issue.body = "Fix this thing"
        mock_issue.labels = []
        mock_issue.user.login = "alice"
        return mock_issue

    def test_posts_summary_on_success(self):
        mock_issue = self._base_patches()
        with patch("clayde.tasks.work.get_github_client"), \
             patch("clayde.tasks.work.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.work.get_issue_state", return_value={}), \
             patch("clayde.tasks.work.fetch_issue", return_value=mock_issue), \
             patch("clayde.tasks.work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.work.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.work.filter_comments", return_value=[]), \
             patch("clayde.tasks.work.is_issue_visible", return_value=True), \
             patch("clayde.tasks.work.render_template", return_value="prompt"), \
             patch("clayde.tasks.work.update_issue_state"), \
             patch("clayde.tasks.work.invoke_claude",
                   return_value=_make_result('{"summary": "Done the work"}')), \
             patch("clayde.tasks.work.find_open_pr", return_value=None), \
             patch("clayde.tasks.work.post_comment") as mock_post:
            run("https://github.com/o/r/issues/1")

        mock_post.assert_called_once()
        body = mock_post.call_args[0][4]
        assert "Done the work" in body

    def test_propagates_usage_limit_error(self):
        mock_issue = self._base_patches()
        with patch("clayde.tasks.work.get_github_client"), \
             patch("clayde.tasks.work.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.work.get_issue_state", return_value={}), \
             patch("clayde.tasks.work.fetch_issue", return_value=mock_issue), \
             patch("clayde.tasks.work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.work.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.work.filter_comments", return_value=[]), \
             patch("clayde.tasks.work.is_issue_visible", return_value=True), \
             patch("clayde.tasks.work.render_template", return_value="prompt"), \
             patch("clayde.tasks.work.update_issue_state"), \
             patch("clayde.tasks.work.invoke_claude",
                   side_effect=UsageLimitError("limit", cost_eur=0.75)):
            import pytest
            with pytest.raises(UsageLimitError):
                run("https://github.com/o/r/issues/1")

    def test_detects_and_persists_new_pr(self):
        mock_issue = self._base_patches()
        with patch("clayde.tasks.work.get_github_client"), \
             patch("clayde.tasks.work.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.work.get_issue_state", return_value={}), \
             patch("clayde.tasks.work.fetch_issue", return_value=mock_issue), \
             patch("clayde.tasks.work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.work.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.work.filter_comments", return_value=[]), \
             patch("clayde.tasks.work.is_issue_visible", return_value=True), \
             patch("clayde.tasks.work.render_template", return_value="prompt"), \
             patch("clayde.tasks.work.update_issue_state") as mock_update, \
             patch("clayde.tasks.work.invoke_claude",
                   return_value=_make_result('{"summary": "Implemented"}')), \
             patch("clayde.tasks.work.find_open_pr",
                   return_value="https://github.com/o/r/pull/5"), \
             patch("clayde.tasks.work.post_comment"), \
             patch("clayde.tasks.work._assign_reviewer"):
            run("https://github.com/o/r/issues/1")

        # pr_url should be persisted
        pr_update = next(
            (c[0][1] for c in mock_update.call_args_list if "pr_url" in c[0][1]),
            None,
        )
        assert pr_update is not None
        assert pr_update["pr_url"] == "https://github.com/o/r/pull/5"

    def test_assigns_reviewer_for_new_pr(self):
        mock_issue = self._base_patches()
        with patch("clayde.tasks.work.get_github_client"), \
             patch("clayde.tasks.work.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.work.get_issue_state", return_value={}), \
             patch("clayde.tasks.work.fetch_issue", return_value=mock_issue), \
             patch("clayde.tasks.work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.work.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.work.filter_comments", return_value=[]), \
             patch("clayde.tasks.work.is_issue_visible", return_value=True), \
             patch("clayde.tasks.work.render_template", return_value="prompt"), \
             patch("clayde.tasks.work.update_issue_state"), \
             patch("clayde.tasks.work.invoke_claude",
                   return_value=_make_result('{"summary": "Implemented"}')), \
             patch("clayde.tasks.work.find_open_pr",
                   return_value="https://github.com/o/r/pull/5"), \
             patch("clayde.tasks.work.post_comment"), \
             patch("clayde.tasks.work._assign_reviewer") as mock_assign:
            run("https://github.com/o/r/issues/1")

        mock_assign.assert_called_once()

    def test_does_not_reassign_reviewer_for_existing_pr(self):
        mock_issue = self._base_patches()
        existing_pr = "https://github.com/o/r/pull/5"
        with patch("clayde.tasks.work.get_github_client"), \
             patch("clayde.tasks.work.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.work.get_issue_state",
                   return_value={"pr_url": existing_pr}), \
             patch("clayde.tasks.work.fetch_issue", return_value=mock_issue), \
             patch("clayde.tasks.work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.work.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.work.filter_comments", return_value=[]), \
             patch("clayde.tasks.work.is_issue_visible", return_value=True), \
             patch("clayde.tasks.work.get_pr_reviews", return_value=[]), \
             patch("clayde.tasks.work.get_pr_review_comments", return_value=[]), \
             patch("clayde.tasks.work.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.work.render_template", return_value="prompt"), \
             patch("clayde.tasks.work.update_issue_state"), \
             patch("clayde.tasks.work.invoke_claude",
                   return_value=_make_result('{"summary": "Review addressed"}')), \
             patch("clayde.tasks.work.find_open_pr", return_value=existing_pr), \
             patch("clayde.tasks.work.post_comment"), \
             patch("clayde.tasks.work._assign_reviewer") as mock_assign:
            run("https://github.com/o/r/issues/1")

        mock_assign.assert_not_called()

    def test_falls_back_to_raw_output_on_json_parse_failure(self):
        mock_issue = self._base_patches()
        with patch("clayde.tasks.work.get_github_client"), \
             patch("clayde.tasks.work.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.work.get_issue_state", return_value={}), \
             patch("clayde.tasks.work.fetch_issue", return_value=mock_issue), \
             patch("clayde.tasks.work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.work.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.work.filter_comments", return_value=[]), \
             patch("clayde.tasks.work.is_issue_visible", return_value=True), \
             patch("clayde.tasks.work.render_template", return_value="prompt"), \
             patch("clayde.tasks.work.update_issue_state"), \
             patch("clayde.tasks.work.invoke_claude",
                   return_value=_make_result("I just posted a question on the issue.")), \
             patch("clayde.tasks.work.find_open_pr", return_value=None), \
             patch("clayde.tasks.work.post_comment") as mock_post:
            run("https://github.com/o/r/issues/1")

        mock_post.assert_called_once()
        body = mock_post.call_args[0][4]
        assert "I just posted a question" in body

    def test_filters_invisible_issue_body(self):
        mock_issue = self._base_patches()
        mock_issue.user.login = "unknown"  # not whitelisted

        captured_prompt = {}

        def capture_render(**kwargs):
            captured_prompt.update(kwargs)
            return "prompt"

        with patch("clayde.tasks.work.get_github_client"), \
             patch("clayde.tasks.work.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.work.get_issue_state", return_value={}), \
             patch("clayde.tasks.work.fetch_issue", return_value=mock_issue), \
             patch("clayde.tasks.work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.work.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.work.filter_comments", return_value=[]), \
             patch("clayde.tasks.work.is_issue_visible", return_value=False), \
             patch("clayde.tasks.work.render_template",
                   side_effect=lambda name, **kw: (captured_prompt.update(kw), "prompt")[1]), \
             patch("clayde.tasks.work.update_issue_state"), \
             patch("clayde.tasks.work.invoke_claude",
                   return_value=_make_result('{"summary": "done"}')), \
             patch("clayde.tasks.work.find_open_pr", return_value=None), \
             patch("clayde.tasks.work.post_comment"):
            run("https://github.com/o/r/issues/1")

        assert captured_prompt.get("body") == "(filtered)"


class TestFormatReviews:
    def test_formats_review_with_body(self):
        review = MagicMock()
        review.id = 1
        review.user.login = "alice"
        review.state = "CHANGES_REQUESTED"
        review.body = "Please fix the typo"

        result = _format_reviews([review], [])
        assert "@alice" in result
        assert "CHANGES_REQUESTED" in result
        assert "Please fix the typo" in result

    def test_formats_review_with_inline_comments(self):
        review = MagicMock()
        review.id = 1
        review.user.login = "alice"
        review.state = "COMMENTED"
        review.body = ""

        rc = MagicMock()
        rc.pull_request_review_id = 1
        rc.path = "src/main.py"
        rc.line = 42
        rc.body = "This line looks wrong"

        result = _format_reviews([review], [rc])
        assert "src/main.py" in result
        assert "42" in result
        assert "This line looks wrong" in result

    def test_returns_none_placeholder_for_empty(self):
        result = _format_reviews([], [])
        assert result == "(none)"

    def test_skips_review_with_no_body_and_no_comments(self):
        review = MagicMock()
        review.id = 1
        review.user.login = "alice"
        review.state = "APPROVED"
        review.body = ""

        result = _format_reviews([review], [])
        assert result == "(none)"


class TestAssignReviewer:
    def test_assigns_non_self_author(self):
        g = MagicMock()
        with patch("clayde.tasks.work.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.work.get_issue_author", return_value="alice"), \
             patch("clayde.tasks.work.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.work.add_pr_reviewer") as mock_add:
            _assign_reviewer(g, "o", "r", 1, "https://github.com/o/r/pull/5")
        mock_add.assert_called_once_with(g, "o", "r", 5, "alice")

    def test_skips_self_author(self):
        g = MagicMock()
        with patch("clayde.tasks.work.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.work.get_issue_author", return_value="claydecode"), \
             patch("clayde.tasks.work.get_settings",
                   return_value=_mock_settings(github_username="ClaydeCode")), \
             patch("clayde.tasks.work.add_pr_reviewer") as mock_add:
            _assign_reviewer(g, "o", "r", 1, "https://github.com/o/r/pull/5")
        mock_add.assert_not_called()

    def test_tolerates_exception(self):
        g = MagicMock()
        with patch("clayde.tasks.work.parse_pr_url", side_effect=Exception("bad url")):
            # Should not raise
            _assign_reviewer(g, "o", "r", 1, "https://github.com/o/r/pull/5")
