"""Configuration via pydantic-settings."""

import logging
import os
from pathlib import Path

from github import Auth, Github
from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_settings: "Settings | None" = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLAYDE_",
        env_file=os.path.join(os.environ.get("CLAYDE_DIR", "/home/ubuntu/clayde"), "config.env"),
        env_file_encoding="utf-8",
    )

    github_token: str = ""
    github_username: str = "ClaydeCode"
    enabled: bool = False
    whitelisted_users_raw: str = "max-tet,ClaydeCode"
    dir: Path = Path("/home/ubuntu/clayde")

    @computed_field
    @property
    def whitelisted_users(self) -> list[str]:
        return [u.strip() for u in self.whitelisted_users_raw.split(",") if u.strip()]

    @property
    def state_file(self) -> str:
        return str(self.dir / "state.json")

    @property
    def log_file(self) -> str:
        return str(self.dir / "logs" / "agent.log")

    @property
    def repos_dir(self) -> str:
        return str(self.dir / "repos")


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
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger("clayde")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
