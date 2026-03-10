"""Configuration via pydantic-settings."""

import logging
import os
from pathlib import Path

from github import Auth, Github
from pydantic_settings import BaseSettings, SettingsConfigDict

_settings: "Settings | None" = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLAYDE_",
        env_file=Path(os.environ.get("CLAYDE_DIR", Path.cwd())) / "config.env",
        env_file_encoding="utf-8",
    )

    github_token: str = ""
    github_username: str = "ClaydeCode"
    enabled: bool = False
    whitelisted_users: str = "max-tet,ClaydeCode"
    dir: Path = Path(os.environ.get("CLAYDE_DIR", Path.cwd()))
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    @property
    def whitelisted_users_list(self) -> list[str]:
        return [u.strip() for u in self.whitelisted_users.split(",") if u.strip()]

    @property
    def state_file(self) -> Path:
        return self.dir / "state.json"

    @property
    def log_file(self) -> Path:
        return self.dir / "logs" / "agent.log"

    @property
    def repos_dir(self) -> Path:
        return self.dir / "repos"


def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _reset_settings() -> None:
    """Clear the cached settings (for testing)."""
    global _settings
    _settings = None


def get_github_client() -> "Github":
    """Return an authenticated PyGitHub client."""
    return Github(auth=Auth.Token(get_settings().github_token))


def setup_logging() -> None:
    """Configure stdlib logging to append to the agent log file."""
    log_file = get_settings().log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger("clayde")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
