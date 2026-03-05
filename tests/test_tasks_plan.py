"""Tests for clayde.tasks.plan."""

from unittest.mock import MagicMock, patch

from clayde.claude import UsageLimitError
from clayde.tasks.plan import _build_prompt, _post_plan_comment, run


class TestBuildPrompt:
    def test_renders_template_with_issue_data(self):
        g = MagicMock()
        issue = MagicMock()
        issue.title = "Fix bug"
        issue.body = "There is a bug"
        issue.labels = []

        comment = MagicMock()
        comment.user.login = "alice"
        comment.body = "I can confirm this"
        g.get_repo.return_value.get_issue.return_value.get_comments.return_value = [comment]

        prompt = _build_prompt(g, issue, "owner", "repo", 42, "/tmp/repo")
        assert "Fix bug" in prompt
        assert "There is a bug" in prompt
        assert "#42" in prompt or "42" in prompt
        assert "@alice" in prompt
        assert "I can confirm this" in prompt
        assert "/tmp/repo" in prompt

    def test_handles_empty_body(self):
        g = MagicMock()
        issue = MagicMock()
        issue.title = "Title"
        issue.body = None
        issue.labels = []
        g.get_repo.return_value.get_issue.return_value.get_comments.return_value = []

        prompt = _build_prompt(g, issue, "owner", "repo", 1, "/path")
        assert "(empty)" in prompt

    def test_includes_labels(self):
        g = MagicMock()
        issue = MagicMock()
        issue.title = "Title"
        issue.body = "body"
        label = MagicMock()
        label.name = "bug"
        issue.labels = [label]
        g.get_repo.return_value.get_issue.return_value.get_comments.return_value = []

        prompt = _build_prompt(g, issue, "owner", "repo", 1, "/path")
        assert "bug" in prompt


class TestPostPlanComment:
    def test_posts_formatted_comment(self):
        g = MagicMock()
        mock_comment = MagicMock()
        mock_comment.id = 555
        g.get_repo.return_value.get_issue.return_value.create_comment.return_value = mock_comment

        result = _post_plan_comment(g, "owner", "repo", 1, "My plan text")
        assert result == 555
        posted_body = g.get_repo.return_value.get_issue.return_value.create_comment.call_args[0][0]
        assert "## Implementation Plan" in posted_body
        assert "My plan text" in posted_body
        assert "\U0001f44d" in posted_body


class TestRun:
    def test_full_success(self):
        with patch("clayde.tasks.plan.get_github_client") as mock_gc, \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.fetch_issue") as mock_fi, \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", return_value="plan text"), \
             patch("clayde.tasks.plan._post_plan_comment", return_value=999):
            run("https://github.com/o/r/issues/1")

        calls = mock_update.call_args_list
        assert calls[0][0] == ("https://github.com/o/r/issues/1", {"status": "planning", "owner": "o", "repo": "r", "number": 1})
        assert calls[1][0] == ("https://github.com/o/r/issues/1", {"status": "awaiting_approval", "plan_comment_id": 999})

    def test_empty_plan_sets_failed(self):
        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.fetch_issue"), \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", return_value="  "):
            run("url")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "failed"

    def test_usage_limit_sets_interrupted(self):
        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.fetch_issue"), \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", side_effect=UsageLimitError("limit")):
            run("url")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "interrupted"
        assert last_call[0][1]["interrupted_phase"] == "planning"
