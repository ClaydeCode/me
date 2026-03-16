"""Configuration via pydantic-settings."""

import logging
from pathlib import Path

from github import Auth, Github
from pydantic_settings import BaseSettings, SettingsConfigDict

_settings: "Settings | None" = None

APP_DIR = Path("/opt/clayde")
DATA_DIR = Path("/data")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLAYDE_",
        env_file=DATA_DIR / "config.env",
        env_file_encoding="utf-8",
    )

    github_token: str = ""
    github_username: str = "ClaydeCode"
    enabled: bool = False
    whitelisted_users: str = "max-tet,ClaydeCode"
    claude_api_key: str = ""
    claude_model: str = "claude-opus-4-6"
    claude_backend: str = "api"  # "api" or "cli"

    # Claude invocation tuning
    claude_tool_loop_timeout_s: int = 1800
    claude_bash_timeout_s: int = 300
    claude_max_tokens: int = 8192

    # Orchestrator behaviour
    loop_interval_s: int = 300
    implement_max_retries: int = 3

    @property
    def whitelisted_users_list(self) -> list[str]:
        return [u.strip() for u in self.whitelisted_users.split(",") if u.strip()]


def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _reset_settings() -> None:
    """Clear the cached settings (for testing)."""
    global _settings, _logging_initialized
    _settings = None
    _logging_initialized = False


def get_github_client() -> "Github":
    """Return an authenticated PyGitHub client."""
    return Github(auth=Auth.Token(get_settings().github_token))


_logging_initialized = False


def setup_logging() -> None:
    """Configure stdlib logging to append to the agent log file.

    Safe to call multiple times — only adds the handler once.
    """
    global _logging_initialized
    if _logging_initialized:
        return
    _logging_initialized = True

    log_file = DATA_DIR / "logs" / "agent.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger("clayde")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
