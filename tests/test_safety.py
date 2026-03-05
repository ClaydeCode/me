"""Tests for clayde.safety."""

from unittest.mock import MagicMock

import clayde.safety as safety_mod
from clayde.safety import _has_whitelisted_reaction, is_issue_authorized, is_plan_approved


def _make_reaction(content, login):
    r = MagicMock()
    r.content = content
    r.user.login = login
    return r


class TestIsIssueAuthorized:
    def test_whitelisted_author(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        issue = MagicMock()
        issue.user.login = "alice"
        assert is_issue_authorized(issue) is True

    def test_non_whitelisted_author_with_thumbsup(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        issue = MagicMock()
        issue.user.login = "bob"
        issue.get_reactions.return_value = [_make_reaction("+1", "alice")]
        assert is_issue_authorized(issue) is True

    def test_non_whitelisted_author_without_approval(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        issue = MagicMock()
        issue.user.login = "bob"
        issue.get_reactions.return_value = [_make_reaction("+1", "charlie")]
        assert is_issue_authorized(issue) is False

    def test_no_reactions(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        issue = MagicMock()
        issue.user.login = "bob"
        issue.get_reactions.return_value = []
        assert is_issue_authorized(issue) is False


class TestIsPlanApproved:
    def test_approved_with_thumbsup(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        g = MagicMock()
        comment = MagicMock()
        comment.get_reactions.return_value = [_make_reaction("+1", "alice")]
        g.get_repo.return_value.get_issue.return_value.get_comment.return_value = comment
        assert is_plan_approved(g, "owner", "repo", 1, 100) is True

    def test_not_approved_wrong_reaction(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        g = MagicMock()
        comment = MagicMock()
        comment.get_reactions.return_value = [_make_reaction("heart", "alice")]
        g.get_repo.return_value.get_issue.return_value.get_comment.return_value = comment
        assert is_plan_approved(g, "owner", "repo", 1, 100) is False


class TestHasWhitelistedReaction:
    def test_matching_reaction(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        reactions = [_make_reaction("+1", "alice")]
        assert _has_whitelisted_reaction(reactions) is True

    def test_wrong_content(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        reactions = [_make_reaction("-1", "alice")]
        assert _has_whitelisted_reaction(reactions) is False

    def test_wrong_user(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        reactions = [_make_reaction("+1", "bob")]
        assert _has_whitelisted_reaction(reactions) is False

    def test_empty_reactions(self, monkeypatch):
        monkeypatch.setattr(safety_mod, "WHITELISTED_USERS", ["alice"])
        assert _has_whitelisted_reaction([]) is False
