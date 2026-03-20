"""Tests for clayde.github."""

from unittest.mock import MagicMock, patch

import pytest
from github import GithubException

from clayde.github import (
    _has_open_parent_issue,
    add_pr_reviewer,
    create_pull_request,
    edit_comment,
    fetch_comment,
    fetch_issue,
    fetch_issue_comments,
    find_open_pr,
    get_assigned_issues,
    get_default_branch,
    get_issue_author,
    get_pr_review_comments,
    get_pr_reviews,
    is_blocked,
    parse_issue_url,
    parse_pr_url,
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


class TestParsePrUrl:
    def test_valid_url(self):
        owner, repo, number = parse_pr_url("https://github.com/alice/myrepo/pull/5")
        assert owner == "alice"
        assert repo == "myrepo"
        assert number == 5

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot parse PR URL"):
            parse_pr_url("https://github.com/alice/repo/issues/1")


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


class TestEditComment:
    def test_calls_edit(self):
        g = MagicMock()
        edit_comment(g, "alice", "repo", 5, 999, "new body")
        g.get_repo.return_value.get_issue.return_value.get_comment.assert_called_once_with(999)
        g.get_repo.return_value.get_issue.return_value.get_comment.return_value.edit.assert_called_once_with("new body")


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


class TestIsBlocked:
    def test_not_blocked_when_no_open_parent(self):
        g = MagicMock()
        settings = MagicMock()
        settings.github_token = "tok"
        with patch("clayde.github._has_open_parent_issue", return_value=False), \
             patch("clayde.github.get_settings", return_value=settings):
            assert is_blocked(g, "o", "r", 1) is False

    def test_blocked_when_open_parent_exists(self):
        g = MagicMock()
        settings = MagicMock()
        settings.github_token = "tok"
        with patch("clayde.github._has_open_parent_issue", return_value=True), \
             patch("clayde.github.get_settings", return_value=settings):
            assert is_blocked(g, "o", "r", 1) is True

    def test_not_blocked_when_no_token(self):
        g = MagicMock()
        settings = MagicMock()
        settings.github_token = None
        with patch("clayde.github.get_settings", return_value=settings):
            assert is_blocked(g, "o", "r", 1) is False

    def test_exception_does_not_block(self):
        g = MagicMock()
        settings = MagicMock()
        settings.github_token = "tok"
        with patch("clayde.github._has_open_parent_issue", side_effect=Exception("fail")), \
             patch("clayde.github.get_settings", return_value=settings):
            assert is_blocked(g, "o", "r", 1) is False


class TestHasOpenParentIssue:
    def test_open_parent_detected(self):
        events = [
            {"event": "connected", "source": {"issue": {"state": "open", "html_url": "https://github.com/o/r/issues/5"}}},
        ]
        with patch("clayde.github._fetch_timeline_events", return_value=events):
            assert _has_open_parent_issue("tok", "o", "r", 1) is True

    def test_closed_parent_not_blocking(self):
        events = [
            {"event": "connected", "source": {"issue": {"state": "closed", "html_url": "https://github.com/o/r/issues/5"}}},
        ]
        with patch("clayde.github._fetch_timeline_events", return_value=events):
            assert _has_open_parent_issue("tok", "o", "r", 1) is False

    def test_no_connected_events(self):
        events = [{"event": "labeled"}, {"event": "assigned"}]
        with patch("clayde.github._fetch_timeline_events", return_value=events):
            assert _has_open_parent_issue("tok", "o", "r", 1) is False

    def test_timeline_failure_returns_false(self):
        with patch("clayde.github._fetch_timeline_events", side_effect=Exception("network error")):
            assert _has_open_parent_issue("tok", "o", "r", 1) is False


class TestAddPrReviewer:
    def test_requests_review(self):
        g = MagicMock()
        add_pr_reviewer(g, "alice", "repo", 5, "bob")
        g.get_repo.return_value.get_pull.assert_called_once_with(5)
        g.get_repo.return_value.get_pull.return_value.create_review_request.assert_called_once_with(reviewers=["bob"])

    def test_handles_failure_gracefully(self):
        g = MagicMock()
        g.get_repo.return_value.get_pull.side_effect = GithubException(422, "error", None)
        # Should not raise
        add_pr_reviewer(g, "alice", "repo", 5, "bob")


class TestGetPrReviews:
    def test_returns_reviews(self):
        g = MagicMock()
        reviews = [MagicMock(), MagicMock()]
        g.get_repo.return_value.get_pull.return_value.get_reviews.return_value = reviews
        result = get_pr_reviews(g, "alice", "repo", 5)
        assert result == reviews


class TestGetPrReviewComments:
    def test_returns_review_comments(self):
        g = MagicMock()
        comments = [MagicMock()]
        g.get_repo.return_value.get_pull.return_value.get_review_comments.return_value = comments
        result = get_pr_review_comments(g, "alice", "repo", 5)
        assert result == comments


class TestGetIssueAuthor:
    def test_returns_author_login(self):
        g = MagicMock()
        g.get_repo.return_value.get_issue.return_value.user.login = "alice"
        assert get_issue_author(g, "o", "r", 1) == "alice"
