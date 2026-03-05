"""Tests for clayde.config."""

import logging
import os
from unittest.mock import mock_open, patch

from clayde import config


class TestLoadConfig:
    def test_parses_key_value_pairs(self, monkeypatch):
        fake_env = "KEY1=value1\nKEY2=value2\n"
        monkeypatch.setattr(config, "WHITELISTED_USERS", [])
        with patch("builtins.open", mock_open(read_data=fake_env)):
            result = config.load_config()
        assert result["KEY1"] == "value1"
        assert result["KEY2"] == "value2"

    def test_skips_comments_and_blank_lines(self, monkeypatch):
        fake_env = "# comment\n\n  \nKEY=val\n"
        monkeypatch.setattr(config, "WHITELISTED_USERS", [])
        with patch("builtins.open", mock_open(read_data=fake_env)):
            result = config.load_config()
        assert result == {"KEY": "val"}

    def test_populates_whitelisted_users(self, monkeypatch):
        fake_env = "WHITELISTED_USERS=alice,bob, charlie \n"
        monkeypatch.setattr(config, "WHITELISTED_USERS", [])
        with patch("builtins.open", mock_open(read_data=fake_env)):
            config.load_config()
        assert config.WHITELISTED_USERS == ["alice", "bob", "charlie"]

    def test_default_whitelisted_users_when_missing(self, monkeypatch):
        fake_env = "SOME_KEY=val\n"
        monkeypatch.setattr(config, "WHITELISTED_USERS", [])
        with patch("builtins.open", mock_open(read_data=fake_env)):
            config.load_config()
        assert config.WHITELISTED_USERS == ["max-tet", "ClaydeCode"]

    def test_value_with_equals_sign(self, monkeypatch):
        fake_env = "TOKEN=abc=def=ghi\n"
        monkeypatch.setattr(config, "WHITELISTED_USERS", [])
        with patch("builtins.open", mock_open(read_data=fake_env)):
            result = config.load_config()
        assert result["TOKEN"] == "abc=def=ghi"


class TestGetGithubClient:
    def test_uses_gh_token_from_env(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "test-token-123")
        with patch("clayde.config.Github") as mock_gh, \
             patch("clayde.config.Auth.Token") as mock_token:
            mock_token.return_value = "auth-obj"
            config.get_github_client()
            mock_token.assert_called_once_with("test-token-123")
            mock_gh.assert_called_once_with(auth="auth-obj")


class TestSetupLogging:
    def test_creates_handler_and_configures_logger(self, tmp_path, monkeypatch):
        log_file = str(tmp_path / "logs" / "agent.log")
        monkeypatch.setattr(config, "LOG_FILE", log_file)
        config.setup_logging()
        logger = logging.getLogger("clayde")
        assert logger.level == logging.INFO
        assert any(
            isinstance(h, logging.FileHandler) and h.baseFilename == log_file
            for h in logger.handlers
        )
        # Clean up handler to avoid affecting other tests
        for h in logger.handlers[:]:
            if isinstance(h, logging.FileHandler) and h.baseFilename == log_file:
                logger.removeHandler(h)
                h.close()
