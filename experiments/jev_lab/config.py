"""Loads Jev Lab configuration without mutating or printing process secrets."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


class ConfigurationError(RuntimeError):
    """Reports a missing or unusable local harness configuration."""


def repository_root() -> Path:
    """Return the repository root from this package's stable location."""

    return Path(__file__).resolve().parents[2]


def load_api_key(root: Path | None = None) -> str:
    """Load the API key from the environment, then the repository .env file."""

    # Environment variables win so CI and temporary overrides need no file change.
    environment_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if environment_key:
        return environment_key

    # dotenv_values reads the file without mutating the current process environment.
    values = dotenv_values((root or repository_root()) / ".env")
    file_key = (values.get("TYPESAFE_API_KEY") or "").strip()
    if file_key:
        return file_key

    raise ConfigurationError(
        "TYPESAFE_API_KEY is missing. Set it in the environment or repository .env file."
    )
