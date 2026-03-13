"""Tests for clayde.state."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import clayde.state as state_mod


class TestLoadState:
    def test_returns_empty_when_file_missing(self, tmp_path):
        sf = tmp_path / "nonexistent.json"
        with patch.object(state_mod, "_STATE_FILE", sf):
            assert state_mod.load_state() == {"issues": {}}

    def test_loads_existing_state(self, tmp_path):
        sf = tmp_path / "state.json"
        data = {"issues": {"url1": {"status": "done"}}}
        sf.write_text(json.dumps(data))
        with patch.object(state_mod, "_STATE_FILE", sf):
            assert state_mod.load_state() == data


class TestSaveState:
    def test_writes_json(self, tmp_path):
        sf = tmp_path / "state.json"
        with patch.object(state_mod, "_STATE_FILE", sf):
            data = {"issues": {"url": {"status": "planning"}}}
            state_mod.save_state(data)
        assert json.loads(sf.read_text()) == data


class TestGetIssueState:
    def test_returns_empty_for_unknown_url(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            assert state_mod.get_issue_state("unknown") == {}

    def test_returns_entry_for_known_url(self, tmp_path):
        sf = tmp_path / "state.json"
        entry = {"status": "done", "pr_url": "https://example.com"}
        sf.write_text(json.dumps({"issues": {"url1": entry}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            assert state_mod.get_issue_state("url1") == entry


class TestUpdateIssueState:
    def test_creates_new_entry(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            state_mod.update_issue_state("url1", {"status": "planning", "owner": "o"})
        result = json.loads(sf.read_text())
        assert result["issues"]["url1"]["status"] == "planning"
        assert result["issues"]["url1"]["owner"] == "o"

    def test_merges_updates(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {"url1": {"status": "planning", "owner": "o"}}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            state_mod.update_issue_state("url1", {"status": "done", "pr_url": "pr"})
        result = json.loads(sf.read_text())
        assert result["issues"]["url1"] == {"status": "done", "owner": "o", "pr_url": "pr"}


class TestAccumulateCost:
    def test_accumulates_on_new_issue(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            state_mod.accumulate_cost("url1", 1.50)
        result = json.loads(sf.read_text())
        assert result["issues"]["url1"]["accumulated_cost_eur"] == pytest.approx(1.50)

    def test_accumulates_on_existing_issue(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {"url1": {"status": "interrupted"}}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            state_mod.accumulate_cost("url1", 1.00)
            state_mod.accumulate_cost("url1", 0.50)
        result = json.loads(sf.read_text())
        assert result["issues"]["url1"]["accumulated_cost_eur"] == pytest.approx(1.50)
        # Existing fields are preserved
        assert result["issues"]["url1"]["status"] == "interrupted"

    def test_accumulates_multiple_times(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            state_mod.accumulate_cost("url1", 0.10)
            state_mod.accumulate_cost("url1", 0.20)
            state_mod.accumulate_cost("url1", 0.30)
        result = json.loads(sf.read_text())
        assert result["issues"]["url1"]["accumulated_cost_eur"] == pytest.approx(0.60)


class TestPopAccumulatedCost:
    def test_returns_and_resets_cost(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {"url1": {"accumulated_cost_eur": 2.50, "status": "ok"}}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            cost = state_mod.pop_accumulated_cost("url1")
        assert cost == pytest.approx(2.50)
        result = json.loads(sf.read_text())
        assert "accumulated_cost_eur" not in result["issues"]["url1"]
        # Other fields preserved
        assert result["issues"]["url1"]["status"] == "ok"

    def test_returns_zero_when_no_cost(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {"url1": {"status": "ok"}}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            cost = state_mod.pop_accumulated_cost("url1")
        assert cost == 0.0

    def test_returns_zero_for_unknown_issue(self, tmp_path):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            cost = state_mod.pop_accumulated_cost("unknown")
        assert cost == 0.0

    def test_accumulate_then_pop(self, tmp_path):
        """Full cycle: accumulate across interruptions, then pop the total."""
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"issues": {}}))
        with patch.object(state_mod, "_STATE_FILE", sf):
            state_mod.accumulate_cost("url1", 1.00)
            state_mod.accumulate_cost("url1", 2.00)
            total = state_mod.pop_accumulated_cost("url1")
        assert total == pytest.approx(3.00)
        # After pop, accumulator is reset
        with patch.object(state_mod, "_STATE_FILE", sf):
            assert state_mod.pop_accumulated_cost("url1") == 0.0
