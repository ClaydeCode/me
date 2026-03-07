"""Tests for clayde.github."""

from unittest.mock import MagicMock, patch

import pytest
from github import GithubException

from clayde.github import (
    create_pull_request,
    extract_branch_name,
    fetch_comment,
    fetch_issue,
    fetch_issue_comments,
    find_open_pr,
    get_assigned_issues,
    get_default_branch,
    parse_issue_url,
    post_comment,
)


class TestParseIssueUrl:
    def test_valid_url(self):
        owner, repo, number = parse_issue_url("https://github.com/alice/myrepo/issues/42")
        assert owner == "alice"
        assert repo == "myrepo"
        assert number == 42

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_issue_url("https://example.com/not-a-github-url")

    def test_pr_url_raises(self):
        with pytest.raises(ValueError):
            parse_issue_url("https://github.com/alice/repo/pull/1")


class TestFetchIssue:
    def test_calls_correct_api(self):
        g = MagicMock()
        mock_issue = MagicMock()
        g.get_repo.return_value.get_issue.return_value = mock_issue
        result = fetch_issue(g, "alice", "repo", 5)
        g.get_repo.assert_called_once_with("alice/repo")
        g.get_repo.return_value.get_issue.assert_called_once_with(5)
        assert result is mock_issue


class TestFetchIssueComments:
    def test_returns_list(self):
        g = MagicMock()
        mock_comments = [MagicMock(), MagicMock()]
        g.get_repo.return_value.get_issue.return_value.get_comments.return_value = mock_comments
        result = fetch_issue_comments(g, "alice", "repo", 5)
        assert result == mock_comments


class TestPostComment:
    def test_returns_comment_id(self):
        g = MagicMock()
        mock_comment = MagicMock()
        mock_comment.id = 12345
        g.get_repo.return_value.get_issue.return_value.create_comment.return_value = mock_comment
        result = post_comment(g, "alice", "repo", 5, "hello")
        g.get_repo.return_value.get_issue.return_value.create_comment.assert_called_once_with("hello")
        assert result == 12345


class TestFetchComment:
    def test_calls_correct_api(self):
        g = MagicMock()
        mock_comment = MagicMock()
        g.get_repo.return_value.get_issue.return_value.get_comment.return_value = mock_comment
        result = fetch_comment(g, "alice", "repo", 5, 999)
        g.get_repo.return_value.get_issue.return_value.get_comment.assert_called_once_with(999)
        assert result is mock_comment


class TestGetDefaultBranch:
    def test_returns_branch_name(self):
        g = MagicMock()
        g.get_repo.return_value.default_branch = "main"
        assert get_default_branch(g, "alice", "repo") == "main"


class TestGetAssignedIssues:
    def test_returns_issues(self):
        g = MagicMock()
        issues = [MagicMock(), MagicMock()]
        g.get_user.return_value.get_issues.return_value = issues
        result = get_assigned_issues(g)
        assert result == issues

    def test_returns_empty_on_exception(self):
        g = MagicMock()
        g.get_user.return_value.get_issues.side_effect = GithubException(500, "error", None)
        result = get_assigned_issues(g)
        assert result == []


class TestExtractBranchName:
    def test_extracts_from_plan(self):
        plan = "Some plan text\n\n**Branch:** `clayde/issue-13-better-branch`\n"
        assert extract_branch_name(plan, 13) == "clayde/issue-13-better-branch"

    def test_fallback_when_missing(self):
        plan = "Some plan text without branch name"
        assert extract_branch_name(plan, 7) == "clayde/issue-7"

    def test_extracts_with_surrounding_text(self):
        plan = "Plan\n**Branch:** `clayde/issue-5-fix-bug`\nMore text"
        assert extract_branch_name(plan, 5) == "clayde/issue-5-fix-bug"


class TestFindOpenPr:
    def test_returns_url_when_pr_exists(self):
        g = MagicMock()
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/alice/repo/pull/10"
        g.get_repo.return_value.get_pulls.return_value = [mock_pr]
        result = find_open_pr(g, "alice", "repo", "clayde/issue-5-fix-bug")
        g.get_repo.return_value.get_pulls.assert_called_once_with(
            state="open", head="alice:clayde/issue-5-fix-bug"
        )
        assert result == "https://github.com/alice/repo/pull/10"

    def test_returns_none_when_no_pr(self):
        g = MagicMock()
        g.get_repo.return_value.get_pulls.return_value = []
        assert find_open_pr(g, "alice", "repo", "clayde/issue-5-fix-bug") is None


class TestCreatePullRequest:
    def test_creates_pr_and_returns_url(self):
        g = MagicMock()
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/alice/repo/pull/11"
        g.get_repo.return_value.create_pull.return_value = mock_pr
        result = create_pull_request(
            g, "alice", "repo",
            title="Fix #5: bug", body="Closes #5",
            head="clayde/issue-5", base="main",
        )
        g.get_repo.return_value.create_pull.assert_called_once_with(
            title="Fix #5: bug", body="Closes #5",
            head="clayde/issue-5", base="main",
        )
        assert result == "https://github.com/alice/repo/pull/11"
