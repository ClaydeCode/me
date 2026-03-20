"""Tests for clayde.config."""

import logging
from pathlib import Path
from unittest.mock import patch

import clayde.config
from clayde.config import Settings, _reset_settings, get_settings, setup_logging


class TestSettings:
    def test_loads_from_env_vars(self, monkeypatch):
        monkeypatch.setenv("CLAYDE_GITHUB_TOKEN", "tok123")
        monkeypatch.setenv("CLAYDE_ENABLED", "true")
        monkeypatch.setenv("CLAYDE_WHITELISTED_USERS", "alice,bob")
        s = Settings(_env_file=None)
        assert s.github_token == "tok123"
        assert s.enabled is True
        assert s.whitelisted_users_list == ["alice", "bob"]

    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("CLAYDE_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("CLAYDE_ENABLED", raising=False)
        monkeypatch.delenv("CLAYDE_WHITELISTED_USERS", raising=False)
        monkeypatch.delenv("CLAYDE_GITHUB_USERNAME", raising=False)
        s = Settings(_env_file=None)
        assert s.github_token == ""
        assert s.enabled is False
        assert s.github_username == ""
        assert s.whitelisted_users_list == []

    def test_effective_git_name_falls_back_to_username(self, monkeypatch):
        monkeypatch.setenv("CLAYDE_GITHUB_USERNAME", "my-bot")
        monkeypatch.delenv("CLAYDE_GIT_NAME", raising=False)
        s = Settings(_env_file=None)
        assert s.effective_git_name == "my-bot"

    def test_effective_git_name_uses_explicit_value(self, monkeypatch):
        monkeypatch.setenv("CLAYDE_GITHUB_USERNAME", "my-bot")
        monkeypatch.setenv("CLAYDE_GIT_NAME", "My Bot")
        s = Settings(_env_file=None)
        assert s.effective_git_name == "My Bot"

    def test_loads_from_env_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / "config.env"
        env_file.write_text("CLAYDE_GITHUB_TOKEN=file-tok\nCLAYDE_ENABLED=true\n")
        monkeypatch.delenv("CLAYDE_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("CLAYDE_ENABLED", raising=False)
        s = Settings(_env_file=str(env_file))
        assert s.github_token == "file-tok"
        assert s.enabled is True

    def test_comma_separated_whitelisted_users(self, monkeypatch):
        monkeypatch.setenv("CLAYDE_WHITELISTED_USERS", "alice, bob , charlie")
        s = Settings(_env_file=None)
        assert s.whitelisted_users_list == ["alice", "bob", "charlie"]

    def test_data_dir_paths(self):
        from clayde.config import DATA_DIR
        assert DATA_DIR / "state.json" == Path("/data/state.json")
        assert DATA_DIR / "logs" / "agent.log" == Path("/data/logs/agent.log")
        assert DATA_DIR / "repos" == Path("/data/repos")

    def test_value_with_equals_sign(self, tmp_path, monkeypatch):
        env_file = tmp_path / "config.env"
        env_file.write_text("CLAYDE_GITHUB_TOKEN=abc=def=ghi\n")
        monkeypatch.delenv("CLAYDE_GITHUB_TOKEN", raising=False)
        s = Settings(_env_file=str(env_file))
        assert s.github_token == "abc=def=ghi"


class TestGetSettings:
    def test_returns_singleton(self, monkeypatch):
        _reset_settings()
        monkeypatch.setenv("CLAYDE_GITHUB_TOKEN", "tok")
        with patch("clayde.config.Settings", wraps=Settings) as mock_cls:
            mock_cls.side_effect = lambda **kw: Settings(_env_file=None)
            s1 = get_settings()
            s2 = get_settings()
        assert s1 is s2
        _reset_settings()

    def test_reset_clears_cache(self, monkeypatch):
        _reset_settings()
        monkeypatch.setenv("CLAYDE_GITHUB_TOKEN", "tok1")
        with patch("clayde.config.Settings", side_effect=lambda **kw: Settings(_env_file=None)):
            s1 = get_settings()
        _reset_settings()
        monkeypatch.setenv("CLAYDE_GITHUB_TOKEN", "tok2")
        with patch("clayde.config.Settings", side_effect=lambda **kw: Settings(_env_file=None)):
            s2 = get_settings()
        assert s1 is not s2
        _reset_settings()


class TestGetGithubClient:
    def test_uses_token_from_settings(self, monkeypatch):
        _reset_settings()
        monkeypatch.setenv("CLAYDE_GITHUB_TOKEN", "test-token-123")
        with patch("clayde.config.Settings", side_effect=lambda **kw: Settings(_env_file=None)):
            from clayde.config import get_github_client
            with patch("clayde.config.Github") as mock_gh, \
                 patch("clayde.config.Auth.Token") as mock_token:
                mock_token.return_value = "auth-obj"
                get_github_client()
                mock_token.assert_called_once_with("test-token-123")
                mock_gh.assert_called_once_with(auth="auth-obj")
        _reset_settings()


class TestSetupLogging:
    def test_creates_handler_and_configures_logger(self, tmp_path, monkeypatch):
        _reset_settings()
        monkeypatch.setattr(clayde.config, "DATA_DIR", tmp_path)
        setup_logging()
        log_file = str(tmp_path / "logs" / "agent.log")
        logger = logging.getLogger("clayde")
        assert logger.level == logging.INFO
        assert any(
            isinstance(h, logging.FileHandler) and h.baseFilename == log_file
            for h in logger.handlers
        )
        for h in logger.handlers[:]:
            if isinstance(h, logging.FileHandler) and h.baseFilename == log_file:
                logger.removeHandler(h)
                h.close()
        _reset_settings()
