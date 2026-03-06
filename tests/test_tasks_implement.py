"""Tests for clayde.tasks.implement."""

from unittest.mock import MagicMock, patch

from clayde.claude import UsageLimitError
from clayde.tasks.implement import (
    _collect_discussion,
    _extract_pr_url,
    _post_result,
    run,
)


class TestCollectDiscussion:
    def test_collects_comments_after_plan(self):
        plan_comment = MagicMock()
        plan_comment.id = 100

        c1 = MagicMock()
        c1.id = 99
        c1.user.login = "alice"
        c1.body = "before plan"

        c2 = MagicMock()
        c2.id = 100  # the plan comment

        c3 = MagicMock()
        c3.id = 101
        c3.user.login = "bob"
        c3.body = "after plan"

        result = _collect_discussion([c1, c2, c3], 100)
        assert "@bob" in result
        assert "after plan" in result
        assert "before plan" not in result

    def test_no_discussion(self):
        plan = MagicMock()
        plan.id = 100
        result = _collect_discussion([plan], 100)
        assert result == "(none)"

    def test_empty_comments(self):
        assert _collect_discussion([], 100) == "(none)"


class TestExtractPrUrl:
    def test_extracts_from_last_line(self):
        output = "Some output\nhttps://github.com/owner/repo/pull/42"
        assert _extract_pr_url(output) == "https://github.com/owner/repo/pull/42"

    def test_extracts_from_middle(self):
        output = "line1\nPR: https://github.com/owner/repo/pull/10\nline3"
        assert _extract_pr_url(output) == "https://github.com/owner/repo/pull/10"

    def test_returns_none_for_no_url(self):
        assert _extract_pr_url("no pr url here") is None

    def test_returns_none_for_empty(self):
        assert _extract_pr_url("") is None

    def test_returns_none_for_none(self):
        assert _extract_pr_url(None) is None


class TestPostResult:
    def test_posts_with_pr_url(self):
        g = MagicMock()
        _post_result(g, "o", "r", 1, "https://github.com/o/r/pull/5")
        body = g.get_repo.return_value.get_issue.return_value.create_comment.call_args[0][0]
        assert "https://github.com/o/r/pull/5" in body

    def test_posts_pr_url(self):
        g = MagicMock()
        _post_result(g, "o", "r", 1, "https://github.com/o/r/pull/7")
        body = g.get_repo.return_value.get_issue.return_value.create_comment.call_args[0][0]
        assert "https://github.com/o/r/pull/7" in body
        assert "complete" in body.lower()


class TestRun:
    def test_full_success(self):
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue"), \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="output\nhttps://github.com/o/r/pull/5"), \
             patch("clayde.tasks.implement._post_result"):
            mock_fc.return_value.body = "plan text"
            run("https://github.com/o/r/issues/1")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "done"
        assert last_call[0][1]["pr_url"] == "https://github.com/o/r/pull/5"

    def test_usage_limit_sets_interrupted(self):
        with patch("clayde.tasks.implement.get_github_client"), \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue"), \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", side_effect=UsageLimitError("limit")):
            mock_fc.return_value.body = "plan text"
            run("url")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "interrupted"
        assert last_call[0][1]["interrupted_phase"] == "implementing"

    def test_resumes_interrupted_with_existing_pr(self):
        state = {"plan_comment_id": 100, "status": "interrupted"}
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value=state), \
             patch("clayde.tasks.implement.find_open_pr", return_value="https://github.com/o/r/pull/5"), \
             patch("clayde.tasks.implement.post_comment"), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.invoke_claude") as mock_claude:
            run("url")
            mock_claude.assert_not_called()

        mock_update.assert_called_once_with("url", {"status": "done", "pr_url": "https://github.com/o/r/pull/5"})

    def test_no_pr_url_sets_interrupted(self):
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue"), \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="no url here"), \
             patch("clayde.tasks.implement.find_open_pr", return_value=None), \
             patch("clayde.tasks.implement.post_comment"):
            mock_fc.return_value.body = "plan text"
            run("https://github.com/o/r/issues/1")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "interrupted"
        assert last_call[0][1]["interrupted_phase"] == "implementing"
        assert last_call[0][1]["retry_count"] == 1

    def test_no_pr_url_fails_after_max_retries(self):
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100, "retry_count": 2}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue"), \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="no url here"), \
             patch("clayde.tasks.implement.find_open_pr", return_value=None), \
             patch("clayde.tasks.implement.post_comment"):
            mock_fc.return_value.body = "plan text"
            run("https://github.com/o/r/issues/1")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "failed"
        assert last_call[0][1]["retry_count"] == 3

    def test_build_prompt_uses_real_template(self):
        """Test that _build_prompt renders with the real Jinja2 template."""
        from clayde.tasks.implement import _build_prompt

        issue = MagicMock()
        issue.title = "Test issue"
        issue.body = "Fix this bug"

        prompt = _build_prompt(issue, "plan text", "discussion", "owner", "repo", 42, "/tmp/repo", "clayde/issue-42-test-branch")
        assert "Test issue" in prompt
        assert "Fix this bug" in prompt
        assert "plan text" in prompt
        assert "discussion" in prompt
        assert "/tmp/repo" in prompt
        assert "42" in prompt
        assert "clayde/issue-42-test-branch" in prompt
