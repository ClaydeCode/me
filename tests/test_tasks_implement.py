"""Tests for clayde.tasks.implement."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from clayde.claude import UsageLimitError
from clayde.tasks.implement import (
    _assign_reviewer_and_finish,
    _checkout_wip_branch,
    _collect_discussion,
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


class TestAssignReviewerAndFinish:
    def test_assigns_reviewer_and_sets_pr_open(self):
        g = MagicMock()
        span = MagicMock()
        with patch("clayde.tasks.implement.get_issue_author", return_value="alice"), \
             patch("clayde.tasks.implement.parse_pr_url", return_value=("o", "r", 5)), \
             patch("clayde.tasks.implement.add_pr_reviewer") as mock_reviewer, \
             patch("clayde.tasks.implement.post_comment"), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update:
            _assign_reviewer_and_finish(g, "o", "r", 1, "url", "https://github.com/o/r/pull/5", span)

        mock_reviewer.assert_called_once_with(g, "o", "r", 5, "alice")
        mock_update.assert_called_once()
        update_data = mock_update.call_args[0][1]
        assert update_data["status"] == "pr_open"
        assert update_data["pr_url"] == "https://github.com/o/r/pull/5"
        assert update_data["last_seen_review_id"] == 0

    def test_handles_reviewer_failure_gracefully(self):
        g = MagicMock()
        span = MagicMock()
        with patch("clayde.tasks.implement.get_issue_author", side_effect=Exception("fail")), \
             patch("clayde.tasks.implement.post_comment"), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update:
            # Should not raise
            _assign_reviewer_and_finish(g, "o", "r", 1, "url", "https://github.com/o/r/pull/5", span)

        # Status should still be set to pr_open
        mock_update.assert_called_once()
        assert mock_update.call_args[0][1]["status"] == "pr_open"


class TestRun:
    def test_full_success_creates_pr_and_assigns_reviewer(self, tmp_path):
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue") as mock_fi, \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement.filter_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="IMPLEMENTATION_COMPLETE"), \
             patch("clayde.tasks.implement.find_open_pr", return_value=None), \
             patch("clayde.tasks.implement.create_pull_request", return_value="https://github.com/o/r/pull/5") as mock_cpr, \
             patch("clayde.tasks.implement._assign_reviewer_and_finish") as mock_finish, \
             patch("clayde.tasks.implement.DATA_DIR", tmp_path):
            mock_fc.return_value.body = "plan text"
            mock_fi.return_value.title = "Test issue"
            run("https://github.com/o/r/issues/1")

        mock_cpr.assert_called_once()
        mock_finish.assert_called_once()

    def test_existing_pr_reused(self, tmp_path):
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue") as mock_fi, \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement.filter_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="IMPLEMENTATION_COMPLETE"), \
             patch("clayde.tasks.implement.find_open_pr", return_value="https://github.com/o/r/pull/5"), \
             patch("clayde.tasks.implement.create_pull_request") as mock_cpr, \
             patch("clayde.tasks.implement._assign_reviewer_and_finish") as mock_finish, \
             patch("clayde.tasks.implement.DATA_DIR", tmp_path):
            mock_fc.return_value.body = "plan text"
            run("https://github.com/o/r/issues/1")

        mock_cpr.assert_not_called()
        mock_finish.assert_called_once()

    def test_usage_limit_sets_interrupted(self, tmp_path):
        with patch("clayde.tasks.implement.get_github_client"), \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue"), \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement.filter_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", side_effect=UsageLimitError("limit")), \
             patch("clayde.tasks.implement.DATA_DIR", tmp_path):
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
             patch("clayde.tasks.implement._assign_reviewer_and_finish") as mock_finish, \
             patch("clayde.tasks.implement.invoke_claude") as mock_claude:
            run("url")
            mock_claude.assert_not_called()

        mock_finish.assert_called_once()

    def test_pr_creation_failure_sets_interrupted(self, tmp_path):
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue") as mock_fi, \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement.filter_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="IMPLEMENTATION_COMPLETE"), \
             patch("clayde.tasks.implement.find_open_pr", return_value=None), \
             patch("clayde.tasks.implement.create_pull_request", side_effect=Exception("API error")), \
             patch("clayde.tasks.implement.post_comment"), \
             patch("clayde.tasks.implement.DATA_DIR", tmp_path):
            mock_fc.return_value.body = "plan text"
            mock_fi.return_value.title = "Test issue"
            run("https://github.com/o/r/issues/1")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "interrupted"
        assert last_call[0][1]["interrupted_phase"] == "implementing"
        assert last_call[0][1]["retry_count"] == 1

    def test_no_pr_fails_after_max_retries(self, tmp_path):
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100, "retry_count": 2}), \
             patch("clayde.tasks.implement.update_issue_state") as mock_update, \
             patch("clayde.tasks.implement.fetch_issue") as mock_fi, \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement.filter_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="IMPLEMENTATION_COMPLETE"), \
             patch("clayde.tasks.implement.find_open_pr", return_value=None), \
             patch("clayde.tasks.implement.create_pull_request", side_effect=Exception("API error")), \
             patch("clayde.tasks.implement.post_comment"), \
             patch("clayde.tasks.implement.DATA_DIR", tmp_path):
            mock_fc.return_value.body = "plan text"
            mock_fi.return_value.title = "Test issue"
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

    def test_conversation_path_passed_to_invoke_claude(self):
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value={"plan_comment_id": 100}), \
             patch("clayde.tasks.implement.update_issue_state"), \
             patch("clayde.tasks.implement.fetch_issue") as mock_fi, \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement.filter_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="done") as mock_claude, \
             patch("clayde.tasks.implement.find_open_pr", return_value="https://github.com/o/r/pull/5"), \
             patch("clayde.tasks.implement._assign_reviewer_and_finish"), \
             patch("clayde.tasks.implement.DATA_DIR", Path("/tmp/test-data")):
            mock_fc.return_value.body = "plan text"
            mock_fi.return_value.title = "Test"
            run("https://github.com/o/r/issues/1")

        call_kwargs = mock_claude.call_args
        assert call_kwargs.kwargs["branch_name"] is not None
        assert call_kwargs.kwargs["conversation_path"] is not None
        assert "o__r__issue-1" in str(call_kwargs.kwargs["conversation_path"])

    def test_resumed_issue_checks_out_wip_branch(self):
        state = {
            "plan_comment_id": 100,
            "status": "interrupted",
            "branch_name": "clayde/issue-1-fix",
        }
        with patch("clayde.tasks.implement.get_github_client") as mock_gc, \
             patch("clayde.tasks.implement.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.implement.get_issue_state", return_value=state), \
             patch("clayde.tasks.implement.find_open_pr", return_value=None), \
             patch("clayde.tasks.implement.update_issue_state"), \
             patch("clayde.tasks.implement.fetch_issue") as mock_fi, \
             patch("clayde.tasks.implement.get_default_branch", return_value="main"), \
             patch("clayde.tasks.implement.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.implement.fetch_comment") as mock_fc, \
             patch("clayde.tasks.implement.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.implement.filter_comments", return_value=[]), \
             patch("clayde.tasks.implement._build_prompt", return_value="prompt"), \
             patch("clayde.tasks.implement.invoke_claude", return_value="done"), \
             patch("clayde.tasks.implement.create_pull_request", return_value="https://github.com/o/r/pull/5"), \
             patch("clayde.tasks.implement._assign_reviewer_and_finish"), \
             patch("clayde.tasks.implement._checkout_wip_branch") as mock_checkout, \
             patch("clayde.tasks.implement.DATA_DIR", Path("/tmp/test-data")):
            mock_fc.return_value.body = "plan text"
            mock_fi.return_value.title = "Test"
            run("https://github.com/o/r/issues/1")

        mock_checkout.assert_called_once_with("/tmp/repo", "clayde/issue-1-fix")


class TestCheckoutWipBranch:
    def test_checks_out_local_branch(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            if cmd == ["git", "branch", "--list", "clayde/issue-1"]:
                result.stdout = "  clayde/issue-1\n"
            else:
                result.stdout = ""
            return result

        with patch("clayde.tasks.implement.subprocess.run", side_effect=fake_run):
            _checkout_wip_branch("/repo", "clayde/issue-1")

        cmd_strs = [" ".join(c) for c in calls]
        assert any("checkout clayde/issue-1" in s for s in cmd_strs)

    def test_checks_out_remote_branch(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            if cmd == ["git", "branch", "--list", "clayde/issue-1"]:
                result.stdout = ""  # not local
            elif "ls-remote" in cmd:
                result.stdout = "abc123\trefs/heads/clayde/issue-1\n"
            else:
                result.stdout = ""
            return result

        with patch("clayde.tasks.implement.subprocess.run", side_effect=fake_run):
            _checkout_wip_branch("/repo", "clayde/issue-1")

        cmd_strs = [" ".join(c) for c in calls]
        assert any("checkout -b clayde/issue-1 origin/clayde/issue-1" in s for s in cmd_strs)

    def test_no_branch_found_does_nothing(self):
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            return result

        with patch("clayde.tasks.implement.subprocess.run", side_effect=fake_run):
            _checkout_wip_branch("/repo", "clayde/issue-1")  # Should not raise
