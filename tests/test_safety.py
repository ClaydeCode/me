"""Tests for clayde.safety."""

from unittest.mock import MagicMock, patch

from clayde.safety import (
    _has_whitelisted_reaction,
    filter_comments,
    has_visible_content,
    is_comment_visible,
    is_issue_visible,
    is_plan_approved,
)


def _make_reaction(content, login):
    r = MagicMock()
    r.content = content
    r.user.login = login
    return r


def _mock_settings(users):
    s = MagicMock()
    s.whitelisted_users_list = users
    return s


def _make_comment(login, reactions=None):
    c = MagicMock()
    c.user.login = login
    c.get_reactions.return_value = reactions or []
    return c


class TestIsCommentVisible:
    def test_visible_if_whitelisted_author(self):
        c = _make_comment("alice")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_comment_visible(c) is True

    def test_visible_if_whitelisted_thumbsup(self):
        c = _make_comment("bob", [_make_reaction("+1", "alice")])
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_comment_visible(c) is True

    def test_not_visible_without_whitelisted_approval(self):
        c = _make_comment("bob", [_make_reaction("+1", "charlie")])
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_comment_visible(c) is False

    def test_not_visible_with_no_reactions(self):
        c = _make_comment("bob")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_comment_visible(c) is False


class TestFilterComments:
    def test_filters_out_invisible_comments(self):
        visible = _make_comment("alice")
        invisible = _make_comment("bob")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            result = filter_comments([visible, invisible])
        assert result == [visible]

    def test_empty_input(self):
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert filter_comments([]) == []

    def test_all_visible(self):
        c1 = _make_comment("alice")
        c2 = _make_comment("bob", [_make_reaction("+1", "alice")])
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            result = filter_comments([c1, c2])
        assert result == [c1, c2]

    def test_all_filtered_out(self):
        c1 = _make_comment("bob")
        c2 = _make_comment("charlie")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            result = filter_comments([c1, c2])
        assert result == []


class TestIsIssueVisible:
    def test_visible_if_whitelisted_author(self):
        issue = MagicMock()
        issue.user.login = "alice"
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_issue_visible(issue) is True

    def test_visible_if_whitelisted_reaction(self):
        issue = MagicMock()
        issue.user.login = "bob"
        issue.get_reactions.return_value = [_make_reaction("+1", "alice")]
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_issue_visible(issue) is True

    def test_not_visible_without_approval(self):
        issue = MagicMock()
        issue.user.login = "bob"
        issue.get_reactions.return_value = []
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_issue_visible(issue) is False


class TestHasVisibleContent:
    def test_true_when_issue_is_visible(self):
        issue = MagicMock()
        issue.user.login = "alice"
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert has_visible_content(issue, []) is True

    def test_true_when_visible_comments_exist(self):
        issue = MagicMock()
        issue.user.login = "bob"
        issue.get_reactions.return_value = []
        visible_comment = _make_comment("alice")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert has_visible_content(issue, [visible_comment]) is True

    def test_false_when_nothing_visible(self):
        issue = MagicMock()
        issue.user.login = "bob"
        issue.get_reactions.return_value = []
        invisible_comment = _make_comment("charlie")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert has_visible_content(issue, [invisible_comment]) is False

    def test_false_when_no_comments_and_invisible_issue(self):
        issue = MagicMock()
        issue.user.login = "bob"
        issue.get_reactions.return_value = []
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert has_visible_content(issue, []) is False


class TestIsPlanApproved:
    def test_approved_with_thumbsup(self):
        g = MagicMock()
        comment = MagicMock()
        comment.get_reactions.return_value = [_make_reaction("+1", "alice")]
        g.get_repo.return_value.get_issue.return_value.get_comment.return_value = comment
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_plan_approved(g, "owner", "repo", 1, 100) is True

    def test_not_approved_wrong_reaction(self):
        g = MagicMock()
        comment = MagicMock()
        comment.get_reactions.return_value = [_make_reaction("heart", "alice")]
        g.get_repo.return_value.get_issue.return_value.get_comment.return_value = comment
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert is_plan_approved(g, "owner", "repo", 1, 100) is False


class TestHasWhitelistedReaction:
    def test_matching_reaction(self):
        reactions = [_make_reaction("+1", "alice")]
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert _has_whitelisted_reaction(reactions) is True

    def test_wrong_content(self):
        reactions = [_make_reaction("-1", "alice")]
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert _has_whitelisted_reaction(reactions) is False

    def test_wrong_user(self):
        reactions = [_make_reaction("+1", "bob")]
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert _has_whitelisted_reaction(reactions) is False

    def test_empty_reactions(self):
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert _has_whitelisted_reaction([]) is False
