"""Resolve user-data paths and load the TypeSafe key without exposing secrets."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


class ConfigurationError(RuntimeError):
    """Report missing or unusable local CLI configuration."""


def repository_root() -> Path:
    """Return the repository root from this package's stable location."""

    return Path(__file__).resolve().parents[1]


def user_data_directory() -> Path:
    """Keep installed configuration and reports outside the working directory."""

    return Path.home() / "Library" / "Application Support" / "Jev Pilot"


def load_api_key(root: Path | None = None) -> str:
    """Load the key from the environment, checkout .env, then user .env."""

    # Environment variables win so CI and temporary overrides need no file change.
    environment_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if environment_key:
        return environment_key

    paths = [user_data_directory() / ".env"]
    source = root if root is not None else repository_root()
    # A wheel's site-packages parent is not a checkout or a configuration source.
    if root is not None or (source / "pyproject.toml").is_file():
        paths.insert(0, source / ".env")
    for path in paths:
        values = dotenv_values(path)
        file_key = (values.get("TYPESAFE_API_KEY") or "").strip()
        if file_key:
            return file_key

    raise ConfigurationError(
        f"TYPESAFE_API_KEY is missing. Set it in the environment or {user_data_directory() / '.env'} "
        "(or the source checkout's .env file)."
    )
