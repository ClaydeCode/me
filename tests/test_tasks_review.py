"""Tests for clayde.tasks.review."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from clayde.claude import InvocationResult, UsageLimitError
from clayde.tasks.review import _build_prompt, _format_reviews, run


def _make_result(output: str, cost_eur: float = 0.50) -> InvocationResult:
    """Helper to create an InvocationResult for testing."""
    return InvocationResult(output=output, cost_eur=cost_eur, input_tokens=100, output_tokens=50)


def _mock_settings():
    s = MagicMock()
    s.github_username = "ClaydeCode"
    return s


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

    def test_empty_reviews(self):
        result = _format_reviews([], [])
        assert result == "(no review content)"


class TestBuildPrompt:
    def test_renders_template(self):
        issue = MagicMock()
        issue.title = "Fix bug"
        issue.body = "body"
        prompt = _build_prompt(issue, "o", "r", 1, "/path", "clayde/issue-1", "review text")
        assert "Fix bug" in prompt
        assert "review text" in prompt
        assert "clayde/issue-1" in prompt


class TestRun:
    def test_no_reviews_does_nothing(self):
        with patch("clayde.tasks.review.get_github_client") as mock_gc, \
             patch("clayde.tasks.review.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.review.get_issue_state", return_value={
                 "pr_url": "https://github.com/o/r/pull/5",
                 "last_seen_review_id": 0,
             }), \
             patch("clayde.tasks.review.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.review.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.review.get_pr_reviews", return_value=[]), \
             patch("clayde.tasks.review.invoke_claude") as mock_claude:
            run("url")
            mock_claude.assert_not_called()

    def test_addresses_new_review_with_cost(self, tmp_path):
        review = MagicMock()
        review.id = 100
        review.user.login = "alice"
        review.state = "CHANGES_REQUESTED"
        review.body = "Please change X"

        review_comment = MagicMock()
        review_comment.pull_request_review_id = 100
        review_comment.path = "src/file.py"
        review_comment.line = 10
        review_comment.body = "Fix this line"

        with patch("clayde.tasks.review.get_github_client") as mock_gc, \
             patch("clayde.tasks.review.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.review.get_issue_state", return_value={
                 "pr_url": "https://github.com/o/r/pull/5",
                 "last_seen_review_id": 0,
                 "branch_name": "clayde/issue-1",
             }), \
             patch("clayde.tasks.review.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.review.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.review.get_pr_reviews", return_value=[review]), \
             patch("clayde.tasks.review.get_pr_review_comments", return_value=[review_comment]), \
             patch("clayde.tasks.review.update_issue_state") as mock_update, \
             patch("clayde.tasks.review.fetch_issue") as mock_fi, \
             patch("clayde.tasks.review.get_default_branch", return_value="main"), \
             patch("clayde.tasks.review.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.review.invoke_claude", return_value=_make_result("Changes made", cost_eur=1.20)) as mock_claude, \
             patch("clayde.tasks.review.post_comment") as mock_post, \
             patch("clayde.tasks.review.pop_accumulated_cost", return_value=0.0), \
             patch("clayde.tasks.review.DATA_DIR", tmp_path):
            mock_fi.return_value.title = "Fix bug"
            mock_fi.return_value.body = "body"
            run("url")

        mock_claude.assert_called_once()
        mock_post.assert_called_once()
        posted_body = mock_post.call_args[0][4]
        assert "Review addressed" in posted_body
        assert "💸 This task cost 1.20€" in posted_body
        # Should return to pr_open
        last_update = mock_update.call_args_list[-1][0][1]
        assert last_update["status"] == "pr_open"
        assert last_update["last_seen_review_id"] == 100

    def test_approval_marks_done(self):
        review = MagicMock()
        review.id = 100
        review.user.login = "alice"
        review.state = "APPROVED"
        review.body = ""

        with patch("clayde.tasks.review.get_github_client"), \
             patch("clayde.tasks.review.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.review.get_issue_state", return_value={
                 "pr_url": "https://github.com/o/r/pull/5",
                 "last_seen_review_id": 0,
             }), \
             patch("clayde.tasks.review.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.review.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.review.get_pr_reviews", return_value=[review]), \
             patch("clayde.tasks.review.get_pr_review_comments", return_value=[]), \
             patch("clayde.tasks.review.update_issue_state") as mock_update:
            run("url")

        # Should update review id first, then set done
        calls = mock_update.call_args_list
        assert any(c[0][1].get("status") == "done" for c in calls)

    def test_usage_limit_sets_interrupted_and_accumulates_cost(self, tmp_path):
        review = MagicMock()
        review.id = 100
        review.user.login = "alice"
        review.state = "CHANGES_REQUESTED"
        review.body = "Fix it"

        with patch("clayde.tasks.review.get_github_client"), \
             patch("clayde.tasks.review.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.review.get_issue_state", return_value={
                 "pr_url": "https://github.com/o/r/pull/5",
                 "last_seen_review_id": 0,
                 "branch_name": "clayde/issue-1",
             }), \
             patch("clayde.tasks.review.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.review.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.review.get_pr_reviews", return_value=[review]), \
             patch("clayde.tasks.review.get_pr_review_comments", return_value=[]), \
             patch("clayde.tasks.review.update_issue_state") as mock_update, \
             patch("clayde.tasks.review.fetch_issue") as mock_fi, \
             patch("clayde.tasks.review.get_default_branch", return_value="main"), \
             patch("clayde.tasks.review.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.review.invoke_claude", side_effect=UsageLimitError("limit", cost_eur=0.90)), \
             patch("clayde.tasks.review.accumulate_cost") as mock_accum, \
             patch("clayde.tasks.review.DATA_DIR", tmp_path):
            mock_fi.return_value.title = "Fix bug"
            mock_fi.return_value.body = "body"
            run("url")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "interrupted"
        assert last_call[0][1]["interrupted_phase"] == "addressing_review"
        mock_accum.assert_called_once_with("url", 0.90)

    def test_no_pr_url_skips(self):
        with patch("clayde.tasks.review.get_github_client"), \
             patch("clayde.tasks.review.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.review.get_issue_state", return_value={}), \
             patch("clayde.tasks.review.invoke_claude") as mock_claude:
            run("url")
            mock_claude.assert_not_called()

    def test_ignores_own_reviews(self):
        review = MagicMock()
        review.id = 100
        review.user.login = "ClaydeCode"
        review.state = "COMMENTED"
        review.body = "My own review"

        with patch("clayde.tasks.review.get_github_client"), \
             patch("clayde.tasks.review.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.review.get_issue_state", return_value={
                 "pr_url": "https://github.com/o/r/pull/5",
                 "last_seen_review_id": 0,
             }), \
             patch("clayde.tasks.review.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.review.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.review.get_pr_reviews", return_value=[review]), \
             patch("clayde.tasks.review.invoke_claude") as mock_claude:
            run("url")
            mock_claude.assert_not_called()
