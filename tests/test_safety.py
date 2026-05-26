"""Tests for clayde.safety."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from clayde.safety import (
    _has_whitelisted_reaction,
    filter_comments,
    filter_pr_reviews,
    get_new_visible_comments,
    has_visible_content,
    is_comment_visible,
    is_issue_visible,
)


def _make_reaction(content, login):
    r = MagicMock()
    r.content = content
    r.user.login = login
    return r


def _mock_settings(users, github_username="ClaydeCode"):
    s = MagicMock()
    s.whitelisted_users_list = users
    s.github_username = github_username
    return s


def _make_comment(login, reactions=None, created_at=None):
    c = MagicMock()
    c.user.login = login
    c.get_reactions.return_value = reactions or []
    if created_at is not None:
        c.created_at = created_at
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


class TestGetNewVisibleComments:
    def _make_ts(self, year=2024, month=1, day=1, hour=12):
        return datetime(year, month, day, hour, tzinfo=timezone.utc)

    def test_returns_all_non_clayde_when_last_seen_none(self):
        """No last_seen_at (first time) returns all visible non-Clayde comments."""
        c1 = _make_comment("alice", created_at=self._make_ts(hour=10))
        c2 = _make_comment("bob", created_at=self._make_ts(hour=11))
        with patch("clayde.safety.get_settings",
                   return_value=_mock_settings(["alice", "bob"], github_username="ClaydeCode")):
            result = get_new_visible_comments([c1, c2], None)
        assert c1 in result
        assert c2 in result

    def test_excludes_clayde_comments_when_last_seen_none(self):
        clayde_c = _make_comment("ClaydeCode", created_at=self._make_ts())
        visible_c = _make_comment("alice", created_at=self._make_ts())
        with patch("clayde.safety.get_settings",
                   return_value=_mock_settings(["alice", "ClaydeCode"], github_username="ClaydeCode")):
            result = get_new_visible_comments([clayde_c, visible_c], None)
        assert clayde_c not in result
        assert visible_c in result

    def test_returns_comments_newer_than_last_seen(self):
        last_seen = self._make_ts(hour=10)
        old_c = _make_comment("alice", created_at=self._make_ts(hour=9))
        new_c = _make_comment("alice", created_at=self._make_ts(hour=11))
        with patch("clayde.safety.get_settings",
                   return_value=_mock_settings(["alice"], github_username="ClaydeCode")):
            result = get_new_visible_comments([old_c, new_c], last_seen)
        assert old_c not in result
        assert new_c in result

    def test_excludes_invisible_comments(self):
        last_seen = self._make_ts(hour=10)
        invisible = _make_comment("mallory", created_at=self._make_ts(hour=11))
        # mallory not whitelisted and no +1 reaction → invisible
        with patch("clayde.safety.get_settings",
                   return_value=_mock_settings(["alice"], github_username="ClaydeCode")):
            result = get_new_visible_comments([invisible], last_seen)
        assert invisible not in result

    def test_excludes_clayde_own_comments_with_last_seen(self):
        last_seen = self._make_ts(hour=10)
        clayde_c = _make_comment("ClaydeCode", created_at=self._make_ts(hour=11))
        with patch("clayde.safety.get_settings",
                   return_value=_mock_settings(["ClaydeCode"], github_username="ClaydeCode")):
            result = get_new_visible_comments([clayde_c], last_seen)
        assert clayde_c not in result


class TestFilterPrReviews:
    def _make_review(self, login):
        r = MagicMock()
        r.user.login = login
        return r

    def test_includes_whitelisted_reviewer(self):
        review = self._make_review("alice")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            result = filter_pr_reviews([review], "ClaydeCode")
        assert result == [review]

    def test_excludes_non_whitelisted_reviewer(self):
        review = self._make_review("mallory")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            result = filter_pr_reviews([review], "ClaydeCode")
        assert result == []

    def test_excludes_bot_own_review(self):
        review = self._make_review("ClaydeCode")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice", "ClaydeCode"])):
            result = filter_pr_reviews([review], "ClaydeCode")
        assert result == []

    def test_keeps_whitelisted_non_bot(self):
        alice = self._make_review("alice")
        bot = self._make_review("ClaydeCode")
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice", "ClaydeCode"])):
            result = filter_pr_reviews([alice, bot], "ClaydeCode")
        assert result == [alice]

    def test_empty_input(self):
        with patch("clayde.safety.get_settings", return_value=_mock_settings(["alice"])):
            assert filter_pr_reviews([], "ClaydeCode") == []


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
