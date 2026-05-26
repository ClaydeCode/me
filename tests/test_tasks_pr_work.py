"""Tests for clayde.tasks.pr_work — standalone PR review task."""

from unittest.mock import MagicMock, patch

from clayde.claude import InvocationResult, UsageLimitError
from clayde.tasks.pr_work import _format_reviews, run


def _make_result(output: str, cost_eur: float = 0.50) -> InvocationResult:
    return InvocationResult(output=output, cost_eur=cost_eur, input_tokens=100, output_tokens=50)


def _mock_settings(github_username="ClaydeCode"):
    s = MagicMock()
    s.github_username = github_username
    return s


def _make_pr(title="Fix review", body="Some description", branch="clayde/feat"):
    pr = MagicMock()
    pr.title = title
    pr.body = body
    pr.head.ref = branch
    return pr


class TestRun:
    PR_URL = "https://github.com/o/r/pull/80"

    def _base_patches(self, pr=None):
        if pr is None:
            pr = _make_pr()
        repo_obj = MagicMock()
        repo_obj.get_pull.return_value = pr
        g = MagicMock()
        g.get_repo.return_value = repo_obj
        return g, pr

    def test_posts_summary_on_success(self):
        g, pr = self._base_patches()
        with patch("clayde.tasks.pr_work.get_github_client", return_value=g), \
             patch("clayde.tasks.pr_work.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.tasks.pr_work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.pr_work.get_pr_reviews", return_value=[]), \
             patch("clayde.tasks.pr_work.get_pr_review_comments", return_value=[]), \
             patch("clayde.tasks.pr_work.filter_pr_reviews", return_value=[]), \
             patch("clayde.tasks.pr_work.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.pr_work.update_issue_state"), \
             patch("clayde.tasks.pr_work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.pr_work.render_template", return_value="prompt"), \
             patch("clayde.tasks.pr_work.invoke_claude",
                   return_value=_make_result('{"summary": "Addressed review"}')), \
             patch("clayde.tasks.pr_work.post_comment") as mock_post:
            run(self.PR_URL)

        mock_post.assert_called_once()
        body = mock_post.call_args[0][4]
        assert "Addressed review" in body

    def test_propagates_usage_limit_error(self):
        import pytest
        g, pr = self._base_patches()
        with patch("clayde.tasks.pr_work.get_github_client", return_value=g), \
             patch("clayde.tasks.pr_work.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.tasks.pr_work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.pr_work.get_pr_reviews", return_value=[]), \
             patch("clayde.tasks.pr_work.get_pr_review_comments", return_value=[]), \
             patch("clayde.tasks.pr_work.filter_pr_reviews", return_value=[]), \
             patch("clayde.tasks.pr_work.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.pr_work.update_issue_state"), \
             patch("clayde.tasks.pr_work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.pr_work.render_template", return_value="prompt"), \
             patch("clayde.tasks.pr_work.invoke_claude",
                   side_effect=UsageLimitError("limit", cost_eur=0.0)):
            with pytest.raises(UsageLimitError):
                run(self.PR_URL)

    def test_persists_metadata_before_invoke(self):
        g, pr = self._base_patches()
        state_calls = []
        with patch("clayde.tasks.pr_work.get_github_client", return_value=g), \
             patch("clayde.tasks.pr_work.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.tasks.pr_work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.pr_work.get_pr_reviews", return_value=[]), \
             patch("clayde.tasks.pr_work.get_pr_review_comments", return_value=[]), \
             patch("clayde.tasks.pr_work.filter_pr_reviews", return_value=[]), \
             patch("clayde.tasks.pr_work.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.pr_work.update_issue_state",
                   side_effect=lambda url, upd: state_calls.append((url, upd))), \
             patch("clayde.tasks.pr_work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.pr_work.render_template", return_value="prompt"), \
             patch("clayde.tasks.pr_work.invoke_claude",
                   return_value=_make_result('{"summary": "done"}')), \
             patch("clayde.tasks.pr_work.post_comment"):
            run(self.PR_URL)

        # metadata update should have happened
        assert any("is_standalone_pr" in upd for _, upd in state_calls)
        first_update = state_calls[0][1]
        assert first_update.get("is_standalone_pr") is True
        assert first_update.get("number") == 80

    def test_falls_back_to_raw_output_on_json_parse_failure(self):
        g, pr = self._base_patches()
        with patch("clayde.tasks.pr_work.get_github_client", return_value=g), \
             patch("clayde.tasks.pr_work.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.tasks.pr_work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.pr_work.get_pr_reviews", return_value=[]), \
             patch("clayde.tasks.pr_work.get_pr_review_comments", return_value=[]), \
             patch("clayde.tasks.pr_work.filter_pr_reviews", return_value=[]), \
             patch("clayde.tasks.pr_work.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.pr_work.update_issue_state"), \
             patch("clayde.tasks.pr_work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.pr_work.render_template", return_value="prompt"), \
             patch("clayde.tasks.pr_work.invoke_claude",
                   return_value=_make_result("I pushed a fix to the branch.")), \
             patch("clayde.tasks.pr_work.post_comment") as mock_post:
            run(self.PR_URL)

        mock_post.assert_called_once()
        body = mock_post.call_args[0][4]
        assert "I pushed a fix" in body

    def test_filters_reviews_by_whitelist(self):
        g, pr = self._base_patches()
        review = MagicMock()
        review.id = 1
        review.user.login = "max-tet"
        review.body = "Please fix"
        visible = [review]

        captured = {}

        def capture_render(**kwargs):
            captured.update(kwargs)
            return "prompt"

        with patch("clayde.tasks.pr_work.get_github_client", return_value=g), \
             patch("clayde.tasks.pr_work.parse_pr_url", return_value=("o", "r", 80)), \
             patch("clayde.tasks.pr_work.get_default_branch", return_value="main"), \
             patch("clayde.tasks.pr_work.get_pr_reviews", return_value=[review]), \
             patch("clayde.tasks.pr_work.get_pr_review_comments", return_value=[]), \
             patch("clayde.tasks.pr_work.filter_pr_reviews", return_value=visible), \
             patch("clayde.tasks.pr_work.get_settings", return_value=_mock_settings()), \
             patch("clayde.tasks.pr_work.update_issue_state"), \
             patch("clayde.tasks.pr_work.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.pr_work.render_template",
                   side_effect=lambda name, **kw: (captured.update(kw), "prompt")[1]), \
             patch("clayde.tasks.pr_work.invoke_claude",
                   return_value=_make_result('{"summary": "done"}')), \
             patch("clayde.tasks.pr_work.post_comment"):
            run(self.PR_URL)

        # The review text should include the visible review
        assert "@max-tet" in captured.get("review_text", "")


class TestFormatReviews:
    def test_formats_review_with_body(self):
        review = MagicMock()
        review.id = 1
        review.user.login = "alice"
        review.state = "CHANGES_REQUESTED"
        review.body = "Please add tests"

        result = _format_reviews([review], [])
        assert "@alice" in result
        assert "CHANGES_REQUESTED" in result
        assert "Please add tests" in result

    def test_formats_inline_comment(self):
        review = MagicMock()
        review.id = 1
        review.user.login = "alice"
        review.state = "COMMENTED"
        review.body = ""

        rc = MagicMock()
        rc.pull_request_review_id = 1
        rc.path = "src/foo.py"
        rc.line = 10
        rc.body = "Rename this"

        result = _format_reviews([review], [rc])
        assert "src/foo.py" in result
        assert "10" in result
        assert "Rename this" in result

    def test_returns_placeholder_for_empty(self):
        assert _format_reviews([], []) == "(none)"

    def test_skips_review_with_no_body_or_comments(self):
        review = MagicMock()
        review.id = 1
        review.user.login = "alice"
        review.state = "APPROVED"
        review.body = ""

        result = _format_reviews([review], [])
        assert result == "(none)"
