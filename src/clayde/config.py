"""Configuration constants, config loading, and logging setup."""

import logging
import os

from github import Auth, Github

CLAYDE_DIR = "/home/ubuntu/clayde"
STATE_FILE = os.path.join(CLAYDE_DIR, "state.json")
LOG_FILE = os.path.join(CLAYDE_DIR, "logs", "agent.log")
REPOS_DIR = os.path.join(CLAYDE_DIR, "repos")
APPROVER = "max-tet"
WHITELISTED_USERS = ["max-tet", "ClaydeCode"]


def load_config():
    """Parse config.env into a dict."""
    config = {}
    with open(os.path.join(CLAYDE_DIR, "config.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
    return config


def get_github_client() -> Github:
    """Return an authenticated PyGitHub client using GH_TOKEN from the environment."""
    return Github(auth=Auth.Token(os.environ["GH_TOKEN"]))


def setup_logging():
    """Configure stdlib logging to append to the agent log file."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger("clayde")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
