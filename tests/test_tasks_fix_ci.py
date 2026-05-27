"""Tests for clayde.tasks.fix_ci — autonomous CI-fix task."""

from unittest.mock import MagicMock, patch

import pytest

from clayde.claude import InvocationResult, UsageLimitError
from clayde.tasks.fix_ci import _format_failed_checks, run


def _make_result(output: str, cost_eur: float = 0.50) -> InvocationResult:
    return InvocationResult(output=output, cost_eur=cost_eur, input_tokens=100, output_tokens=50)


def _mock_issue():
    issue = MagicMock()
    issue.title = "Add a feature"
    issue.body = "Please add it"
    issue.labels = []
    return issue


FAILED = [{"name": "test", "conclusion": "failure", "details_url": "https://ci/log"}]


class TestFormatFailedChecks:
    def test_includes_name_conclusion_and_url(self):
        out = _format_failed_checks(FAILED)
        assert "test" in out
        assert "failure" in out
        assert "https://ci/log" in out

    def test_handles_missing_url(self):
        out = _format_failed_checks([{"name": "lint", "conclusion": "failure"}])
        assert "lint" in out
        assert "—" not in out  # no trailing URL separator

    def test_empty_list(self):
        assert _format_failed_checks([]) == "(none)"


class TestRun:
    def _patches(self, invoke):
        return [
            patch("clayde.tasks.fix_ci.get_github_client"),
            patch("clayde.tasks.fix_ci.parse_issue_url", return_value=("o", "r", 86)),
            patch("clayde.tasks.fix_ci.fetch_issue", return_value=_mock_issue()),
            patch("clayde.tasks.fix_ci.get_default_branch", return_value="main"),
            patch("clayde.tasks.fix_ci.ensure_repo", return_value="/tmp/repo"),
            patch("clayde.tasks.fix_ci.is_issue_visible", return_value=True),
            patch("clayde.tasks.fix_ci.render_template", return_value="prompt"),
            patch("clayde.tasks.fix_ci.invoke_claude", invoke),
        ]

    def test_posts_summary_with_ci_header(self):
        invoke = MagicMock(return_value=_make_result('{"summary": "Fixed the import"}'))
        with patch("clayde.tasks.fix_ci.post_comment") as mock_post:
            for p in self._patches(invoke):
                p.start()
            try:
                run("https://github.com/o/r/issues/86",
                    "https://github.com/o/r/pull/5", "clayde/issue-86", FAILED)
            finally:
                patch.stopall()
        mock_post.assert_called_once()
        body = mock_post.call_args[0][4]
        assert "Fixed the import" in body
        assert "CI was failing" in body
        assert "test" in body

    def test_renders_prompt_with_failed_checks(self):
        invoke = MagicMock(return_value=_make_result('{"summary": "ok"}'))
        with patch("clayde.tasks.fix_ci.render_template", return_value="prompt") as mock_render, \
             patch("clayde.tasks.fix_ci.get_github_client"), \
             patch("clayde.tasks.fix_ci.parse_issue_url", return_value=("o", "r", 86)), \
             patch("clayde.tasks.fix_ci.fetch_issue", return_value=_mock_issue()), \
             patch("clayde.tasks.fix_ci.get_default_branch", return_value="main"), \
             patch("clayde.tasks.fix_ci.ensure_repo", return_value="/tmp/repo"), \
             patch("clayde.tasks.fix_ci.is_issue_visible", return_value=True), \
             patch("clayde.tasks.fix_ci.invoke_claude", invoke), \
             patch("clayde.tasks.fix_ci.post_comment"):
            run("https://github.com/o/r/issues/86",
                "https://github.com/o/r/pull/5", "clayde/issue-86", FAILED)
        kwargs = mock_render.call_args.kwargs
        assert kwargs["branch_name"] == "clayde/issue-86"
        assert kwargs["pr_url"] == "https://github.com/o/r/pull/5"
        assert "test" in kwargs["failed_checks"]

    def test_propagates_usage_limit_error(self):
        invoke = MagicMock(side_effect=UsageLimitError("limit", cost_eur=0.0))
        with patch("clayde.tasks.fix_ci.post_comment"):
            for p in self._patches(invoke):
                p.start()
            try:
                with pytest.raises(UsageLimitError):
                    run("https://github.com/o/r/issues/86",
                        "https://github.com/o/r/pull/5", "clayde/issue-86", FAILED)
            finally:
                patch.stopall()

    def test_falls_back_to_raw_output_on_bad_json(self):
        invoke = MagicMock(return_value=_make_result("not json at all"))
        with patch("clayde.tasks.fix_ci.post_comment") as mock_post:
            for p in self._patches(invoke):
                p.start()
            try:
                run("https://github.com/o/r/issues/86",
                    "https://github.com/o/r/pull/5", "clayde/issue-86", FAILED)
            finally:
                patch.stopall()
        mock_post.assert_called_once()
        assert "not json at all" in mock_post.call_args[0][4]
