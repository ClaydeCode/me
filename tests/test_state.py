"""Tests for clayde.state."""

import json
from unittest.mock import MagicMock, patch

import clayde.state as state_mod


def _mock_settings(state_file):
    s = MagicMock()
    s.state_file = state_file
    return s


class TestLoadState:
    def test_returns_empty_when_file_missing(self, tmp_path):
        sf = str(tmp_path / "nonexistent.json")
        with patch("clayde.state.get_settings", return_value=_mock_settings(sf)):
            assert state_mod.load_state() == {"issues": {}}

    def test_loads_existing_state(self, tmp_path):
        sf = tmp_path / "state.json"
        data = {"issues": {"url1": {"status": "done"}}}
        sf.write_text(json.dumps(data))
        with patch("clayde.state.get_settings", return_value=_mock_settings(str(sf))):
            assert state_mod.load_state() == data


class TestSaveState:
    def test_writes_json(self, tmp_path):
        sf = tmp_path / "state.json"
        with patch("clayde.state.get_settings", return_value=_mock_settings(str(sf))):
            data = {"issues": {"url": {"status": "planning"}}}
            state_mod.save_state(data)
        assert json.loads(sf.read_text()) == data


class TestGetIssueState:
    def test_returns_empty_for_unknown_url(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {}}))
        with patch("clayde.state.get_settings", return_value=_mock_settings(str(sf))):
            assert state_mod.get_issue_state("unknown") == {}

    def test_returns_entry_for_known_url(self, tmp_path):
        sf = tmp_path / "state.json"
        entry = {"status": "done", "pr_url": "https://example.com"}
        sf.write_text(json.dumps({"issues": {"url1": entry}}))
        with patch("clayde.state.get_settings", return_value=_mock_settings(str(sf))):
            assert state_mod.get_issue_state("url1") == entry


class TestUpdateIssueState:
    def test_creates_new_entry(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {}}))
        with patch("clayde.state.get_settings", return_value=_mock_settings(str(sf))):
            state_mod.update_issue_state("url1", {"status": "planning", "owner": "o"})
        result = json.loads(sf.read_text())
        assert result["issues"]["url1"]["status"] == "planning"
        assert result["issues"]["url1"]["owner"] == "o"

    def test_merges_updates(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {"url1": {"status": "planning", "owner": "o"}}}))
        with patch("clayde.state.get_settings", return_value=_mock_settings(str(sf))):
            state_mod.update_issue_state("url1", {"status": "done", "pr_url": "pr"})
        result = json.loads(sf.read_text())
        assert result["issues"]["url1"] == {"status": "done", "owner": "o", "pr_url": "pr"}
