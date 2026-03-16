"""Tests for clayde.tasks.plan — two-phase planning."""

from unittest.mock import MagicMock, patch

from clayde.claude import InvocationResult, UsageLimitError
from clayde.prompts import collect_comments_after
from clayde.tasks.plan import (
    _build_preliminary_prompt,
    _build_thorough_prompt,
    _build_update_prompt,
    _parse_update_output,
    _post_preliminary_comment,
    _post_thorough_plan_comment,
    run_preliminary,
    run_thorough,
    run_update,
)


def _make_result(output: str, cost_eur: float = 0.50) -> InvocationResult:
    """Helper to create an InvocationResult for testing."""
    return InvocationResult(output=output, cost_eur=cost_eur, input_tokens=100, output_tokens=50)


def _mock_settings(users=None):
    s = MagicMock()
    s.whitelisted_users_list = users or ["alice"]
    s.github_username = "ClaydeCode"
    return s


class TestBuildPreliminaryPrompt:
    def test_renders_with_issue_data(self):
        g = MagicMock()
        issue = MagicMock()
        issue.title = "Fix bug"
        issue.body = "There is a bug"
        issue.labels = []
        issue.user.login = "alice"

        comment = MagicMock()
        comment.user.login = "alice"
        comment.body = "I can confirm this"
        comment.get_reactions.return_value = []
        g.get_repo.return_value.get_issue.return_value.get_comments.return_value = [comment]

        settings = _mock_settings()
        with patch("clayde.safety.get_settings", return_value=settings):
            prompt = _build_preliminary_prompt(g, issue, "owner", "repo", 42, "/tmp/repo")
        assert "Fix bug" in prompt
        assert "There is a bug" in prompt
        assert "42" in prompt
        assert "/tmp/repo" in prompt
        # Should be a preliminary plan prompt, not thorough
        assert "preliminary" in prompt.lower() or "short" in prompt.lower()

    def test_filters_invisible_comments(self):
        g = MagicMock()
        issue = MagicMock()
        issue.title = "Title"
        issue.body = "body"
        issue.labels = []
        issue.user.login = "alice"

        visible = MagicMock()
        visible.user.login = "alice"
        visible.body = "visible comment"
        visible.get_reactions.return_value = []

        invisible = MagicMock()
        invisible.user.login = "bob"
        invisible.body = "invisible comment"
        invisible.get_reactions.return_value = []

        g.get_repo.return_value.get_issue.return_value.get_comments.return_value = [visible, invisible]

        settings = _mock_settings()
        with patch("clayde.safety.get_settings", return_value=settings):
            prompt = _build_preliminary_prompt(g, issue, "owner", "repo", 1, "/path")
        assert "visible comment" in prompt
        assert "invisible comment" not in prompt

    def test_filters_issue_body_when_not_visible(self):
        g = MagicMock()
        issue = MagicMock()
        issue.title = "Title"
        issue.body = "secret body"
        issue.labels = []
        issue.user.login = "bob"  # not whitelisted
        issue.get_reactions.return_value = []

        g.get_repo.return_value.get_issue.return_value.get_comments.return_value = []

        settings = _mock_settings()
        with patch("clayde.safety.get_settings", return_value=settings):
            prompt = _build_preliminary_prompt(g, issue, "owner", "repo", 1, "/path")
        assert "secret body" not in prompt
        assert "(filtered)" in prompt


class TestBuildThoroughPrompt:
    def test_renders_with_preliminary_plan(self):
        g = MagicMock()
        issue = MagicMock()
        issue.title = "Fix bug"
        issue.body = "body"
        issue.labels = []
        issue.user.login = "alice"

        settings = _mock_settings()
        with patch("clayde.safety.get_settings", return_value=settings):
            prompt = _build_thorough_prompt(
                g, issue, "owner", "repo", 1, "/path",
                "my preliminary plan", "discussion text",
            )
        assert "my preliminary plan" in prompt
        assert "discussion text" in prompt
        assert "thorough" in prompt.lower() or "detailed" in prompt.lower()


class TestBuildUpdatePrompt:
    def test_renders_with_new_comments(self):
        prompt = _build_update_prompt(
            1, "Title", "o", "r", "body",
            "current plan text", "new comment text", "/path",
            phase="preliminary",
        )
        assert "current plan text" in prompt
        assert "new comment text" in prompt
        assert "UPDATED_PLAN" in prompt

    def test_preliminary_phase_warns_against_escalation(self):
        prompt = _build_update_prompt(
            1, "Title", "o", "r", "body",
            "current plan text", "new comment text", "/path",
            phase="preliminary",
        )
        assert "PRELIMINARY" in prompt
        assert "Do NOT escalate" in prompt

    def test_thorough_phase_maintains_detail(self):
        prompt = _build_update_prompt(
            1, "Title", "o", "r", "body",
            "current plan text", "new comment text", "/path",
            phase="thorough",
        )
        assert "thorough implementation plan" in prompt


class TestPostPreliminaryComment:
    def test_posts_formatted_comment(self):
        g = MagicMock()
        mock_comment = MagicMock()
        mock_comment.id = 555
        g.get_repo.return_value.get_issue.return_value.create_comment.return_value = mock_comment

        result = _post_preliminary_comment(g, "owner", "repo", 1, "My preliminary plan")
        assert result == 555
        posted_body = g.get_repo.return_value.get_issue.return_value.create_comment.call_args[0][0]
        assert "## Preliminary Plan" in posted_body
        assert "My preliminary plan" in posted_body
        assert "\U0001f44d" in posted_body
        assert "preliminary" in posted_body.lower()
        assert "💸" not in posted_body  # no cost line when cost is 0

    def test_posts_with_cost(self):
        g = MagicMock()
        mock_comment = MagicMock()
        mock_comment.id = 555
        g.get_repo.return_value.get_issue.return_value.create_comment.return_value = mock_comment

        result = _post_preliminary_comment(g, "owner", "repo", 1, "plan", cost_eur=1.23)
        posted_body = g.get_repo.return_value.get_issue.return_value.create_comment.call_args[0][0]
        assert "💸 This task cost 1.23€" in posted_body


class TestPostThoroughPlanComment:
    def test_posts_formatted_comment(self):
        g = MagicMock()
        mock_comment = MagicMock()
        mock_comment.id = 666
        g.get_repo.return_value.get_issue.return_value.create_comment.return_value = mock_comment

        result = _post_thorough_plan_comment(g, "owner", "repo", 1, "My thorough plan")
        assert result == 666
        posted_body = g.get_repo.return_value.get_issue.return_value.create_comment.call_args[0][0]
        assert "## Implementation Plan" in posted_body
        assert "My thorough plan" in posted_body
        assert "\U0001f44d" in posted_body
        assert "💸" not in posted_body  # no cost line when cost is 0

    def test_posts_with_cost(self):
        g = MagicMock()
        mock_comment = MagicMock()
        mock_comment.id = 666
        g.get_repo.return_value.get_issue.return_value.create_comment.return_value = mock_comment

        result = _post_thorough_plan_comment(g, "owner", "repo", 1, "plan", cost_eur=3.45)
        posted_body = g.get_repo.return_value.get_issue.return_value.create_comment.call_args[0][0]
        assert "💸 This task cost 3.45€" in posted_body


class TestCollectDiscussionAfter:
    def test_collects_comments_after_id(self):
        c1 = MagicMock()
        c1.id = 100
        c1.user.login = "plan"
        c1.body = "plan text"

        c2 = MagicMock()
        c2.id = 101
        c2.user.login = "alice"
        c2.body = "discussion"

        result = collect_comments_after([c1, c2], 100)
        assert "discussion" in result
        assert "plan text" not in result

    def test_none_when_no_comments_after(self):
        c1 = MagicMock()
        c1.id = 100
        result = collect_comments_after([c1], 100)
        assert result == "(none)"


class TestParseUpdateOutput:
    def test_parses_with_separator(self):
        output = "Here is the summary\n---UPDATED_PLAN---\nHere is the updated plan"
        summary, plan = _parse_update_output(output)
        assert summary == "Here is the summary"
        assert plan == "Here is the updated plan"

    def test_no_separator_returns_all_as_summary(self):
        output = "Just a summary with no updated plan"
        summary, plan = _parse_update_output(output)
        assert summary == output
        assert plan == ""

    def test_empty_output(self):
        summary, plan = _parse_update_output("")
        assert summary == ""
        assert plan == ""


class TestRunPreliminary:
    def test_full_success(self):
        mock_comment = MagicMock()
        mock_comment.id = 500

        with patch("clayde.tasks.plan.get_github_client") as mock_gc, \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.fetch_issue") as mock_fi, \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan._build_preliminary_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", return_value=_make_result("x" * 150, cost_eur=1.00)), \
             patch("clayde.tasks.plan._post_preliminary_comment", return_value=999) as mock_post, \
             patch("clayde.tasks.plan.fetch_issue_comments", return_value=[mock_comment]), \
             patch("clayde.tasks.plan.pop_accumulated_cost", return_value=0.0):
            run_preliminary("https://github.com/o/r/issues/1")

        calls = mock_update.call_args_list
        assert calls[0][0] == ("https://github.com/o/r/issues/1",
                               {"status": "preliminary_planning", "owner": "o", "repo": "r", "number": 1})
        last = calls[-1][0][1]
        assert last["status"] == "awaiting_preliminary_approval"
        assert last["preliminary_comment_id"] == 999
        assert last["last_seen_comment_id"] == 500
        # Cost is passed to the comment helper
        mock_post.assert_called_once()
        assert mock_post.call_args[0][4] == "x" * 150
        assert mock_post.call_args[0][5] == 1.00

    def test_empty_plan_sets_failed(self):
        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.fetch_issue"), \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan._build_preliminary_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", return_value=_make_result("  ")), \
             patch("clayde.tasks.plan.pop_accumulated_cost", return_value=0.0):
            run_preliminary("url")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "failed"

    def test_usage_limit_sets_interrupted_and_accumulates_cost(self):
        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.fetch_issue"), \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan._build_preliminary_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", side_effect=UsageLimitError("limit", cost_eur=0.75)), \
             patch("clayde.tasks.plan.accumulate_cost") as mock_accum:
            run_preliminary("url")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "interrupted"
        assert last_call[0][1]["interrupted_phase"] == "preliminary_planning"
        mock_accum.assert_called_once_with("url", 0.75)

    def test_accumulated_cost_included_on_success(self):
        """Cost from prior interrupted runs is included in the total."""
        mock_comment = MagicMock()
        mock_comment.id = 500

        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state"), \
             patch("clayde.tasks.plan.fetch_issue"), \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan._build_preliminary_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", return_value=_make_result("x" * 150, cost_eur=1.00)), \
             patch("clayde.tasks.plan._post_preliminary_comment", return_value=999) as mock_post, \
             patch("clayde.tasks.plan.fetch_issue_comments", return_value=[mock_comment]), \
             patch("clayde.tasks.plan.pop_accumulated_cost", return_value=2.00):
            run_preliminary("url")

        # Total cost should be accumulated (2.00) + current (1.00) = 3.00
        assert mock_post.call_args[0][5] == 3.00


class TestRunThorough:
    def test_full_success(self):
        mock_comment = MagicMock()
        mock_comment.id = 600

        with patch("clayde.tasks.plan.get_github_client") as mock_gc, \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.fetch_issue") as mock_fi, \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan.get_issue_state", return_value={"preliminary_comment_id": 100}), \
             patch("clayde.tasks.plan.fetch_comment") as mock_fc, \
             patch("clayde.tasks.plan.fetch_issue_comments", return_value=[mock_comment]), \
             patch("clayde.tasks.plan.filter_comments", return_value=[]), \
             patch("clayde.tasks.plan._build_thorough_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", return_value=_make_result("x" * 250, cost_eur=2.00)), \
             patch("clayde.tasks.plan._post_thorough_plan_comment", return_value=888) as mock_post, \
             patch("clayde.tasks.plan.pop_accumulated_cost", return_value=0.0):
            mock_fc.return_value.body = "preliminary plan"
            mock_fi.return_value.labels = []
            run_thorough("https://github.com/o/r/issues/1")

        calls = mock_update.call_args_list
        assert calls[0][0] == ("https://github.com/o/r/issues/1", {"status": "planning"})
        last = calls[-1][0][1]
        assert last["status"] == "awaiting_plan_approval"
        assert last["plan_comment_id"] == 888
        # Cost is passed
        assert mock_post.call_args[0][5] == 2.00

    def test_usage_limit_sets_interrupted_and_accumulates_cost(self):
        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.fetch_issue"), \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan.get_issue_state", return_value={"preliminary_comment_id": 100}), \
             patch("clayde.tasks.plan.fetch_comment") as mock_fc, \
             patch("clayde.tasks.plan.fetch_issue_comments", return_value=[]), \
             patch("clayde.tasks.plan.filter_comments", return_value=[]), \
             patch("clayde.tasks.plan._build_thorough_prompt", return_value="prompt"), \
             patch("clayde.tasks.plan.invoke_claude", side_effect=UsageLimitError("limit", cost_eur=1.50)), \
             patch("clayde.tasks.plan.accumulate_cost") as mock_accum:
            mock_fc.return_value.body = "plan"
            run_thorough("url")

        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "interrupted"
        assert last_call[0][1]["interrupted_phase"] == "planning"
        mock_accum.assert_called_once_with("url", 1.50)


class TestRunUpdate:
    def test_updates_plan_and_posts_summary_with_cost(self):
        new_comment = MagicMock()
        new_comment.id = 300
        new_comment.user.login = "alice"
        new_comment.body = "please change X"

        last_comment = MagicMock()
        last_comment.id = 400

        with patch("clayde.tasks.plan.get_github_client") as mock_gc, \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.get_issue_state", return_value={
                 "preliminary_comment_id": 100,
                 "last_seen_comment_id": 200,
             }), \
             patch("clayde.tasks.plan.fetch_issue") as mock_fi, \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan.fetch_comment") as mock_fc, \
             patch("clayde.tasks.plan.fetch_issue_comments", side_effect=[
                 [new_comment],
                 [last_comment],
             ]), \
             patch("clayde.tasks.plan.get_new_visible_comments", return_value=[new_comment]), \
             patch("clayde.tasks.plan.is_issue_visible", return_value=True), \
             patch("clayde.tasks.plan.invoke_claude",
                   return_value=_make_result("Summary\n---UPDATED_PLAN---\nUpdated plan text", cost_eur=0.80)), \
             patch("clayde.tasks.plan.edit_comment") as mock_edit, \
             patch("clayde.tasks.plan.post_comment") as mock_post, \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.pop_accumulated_cost", return_value=0.0):
            mock_fc.return_value.body = "current plan"
            mock_fi.return_value.body = "issue body"
            mock_fi.return_value.title = "Title"
            run_update("url", "preliminary")

        mock_edit.assert_called_once()
        mock_post.assert_called_once()
        posted_body = mock_post.call_args[0][4]
        assert "Plan updated" in posted_body
        assert "💸 This task cost 0.80€" in posted_body

    def test_usage_limit_accumulates_cost(self):
        new_comment = MagicMock()
        new_comment.id = 300
        new_comment.user.login = "alice"
        new_comment.body = "change something"

        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.get_issue_state", return_value={
                 "preliminary_comment_id": 100,
                 "last_seen_comment_id": 200,
             }), \
             patch("clayde.tasks.plan.fetch_issue") as mock_fi, \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan.fetch_comment") as mock_fc, \
             patch("clayde.tasks.plan.fetch_issue_comments", return_value=[new_comment]), \
             patch("clayde.tasks.plan.get_new_visible_comments", return_value=[new_comment]), \
             patch("clayde.tasks.plan.is_issue_visible", return_value=True), \
             patch("clayde.tasks.plan.invoke_claude", side_effect=UsageLimitError("limit", cost_eur=0.30)), \
             patch("clayde.tasks.plan.update_issue_state") as mock_update, \
             patch("clayde.tasks.plan.accumulate_cost") as mock_accum:
            mock_fc.return_value.body = "plan"
            mock_fi.return_value.body = "body"
            mock_fi.return_value.title = "Title"
            run_update("url", "preliminary")

        mock_accum.assert_called_once_with("url", 0.30)
        last_call = mock_update.call_args_list[-1]
        assert last_call[0][1]["status"] == "interrupted"

    def test_skips_when_no_new_comments(self):
        old_comment = MagicMock()
        old_comment.id = 50
        old_comment.user.login = "alice"

        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.get_issue_state", return_value={
                 "preliminary_comment_id": 100,
                 "last_seen_comment_id": 200,
             }), \
             patch("clayde.tasks.plan.fetch_issue") as mock_fi, \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan.fetch_comment") as mock_fc, \
             patch("clayde.tasks.plan.fetch_issue_comments", return_value=[old_comment]), \
             patch("clayde.tasks.plan.get_new_visible_comments", return_value=[]), \
             patch("clayde.tasks.plan.is_issue_visible", return_value=True), \
             patch("clayde.tasks.plan.invoke_claude") as mock_claude:
            mock_fc.return_value.body = "plan"
            mock_fi.return_value.body = "body"
            mock_fi.return_value.title = "Title"
            run_update("url", "preliminary")

        mock_claude.assert_not_called()

    def test_ignores_clayde_comments(self):
        clayde_comment = MagicMock()
        clayde_comment.id = 300
        clayde_comment.user.login = "ClaydeCode"

        with patch("clayde.tasks.plan.get_github_client"), \
             patch("clayde.tasks.plan.parse_issue_url", return_value=("o", "r", 1)), \
             patch("clayde.tasks.plan.get_issue_state", return_value={
                 "preliminary_comment_id": 100,
                 "last_seen_comment_id": 200,
             }), \
             patch("clayde.tasks.plan.fetch_issue") as mock_fi, \
             patch("clayde.tasks.plan.get_default_branch", return_value="main"), \
             patch("clayde.tasks.plan.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.plan.fetch_comment") as mock_fc, \
             patch("clayde.tasks.plan.fetch_issue_comments", return_value=[clayde_comment]), \
             patch("clayde.tasks.plan.get_new_visible_comments", return_value=[]), \
             patch("clayde.tasks.plan.is_issue_visible", return_value=True), \
             patch("clayde.tasks.plan.invoke_claude") as mock_claude:
            mock_fc.return_value.body = "plan"
            mock_fi.return_value.body = "body"
            mock_fi.return_value.title = "Title"
            run_update("url", "preliminary")

        mock_claude.assert_not_called()
