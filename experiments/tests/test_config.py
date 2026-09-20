"""Tests API-key loading precedence without exposing secret values."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jev_lab.config import ConfigurationError, load_api_key


class ConfigurationTests(unittest.TestCase):
    """Verifies environment-first, repository-.env-second key loading."""

    def test_environment_key_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text(
                "TYPESAFE_API_KEY=file-key\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {"TYPESAFE_API_KEY": " environment-key "}):
                self.assertEqual(load_api_key(Path(directory)), "environment-key")

    def test_reads_key_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text(
                'TYPESAFE_API_KEY="file-key"\n', encoding="utf-8"
            )
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_api_key(Path(directory)), "file-key")

    def test_reports_missing_key_without_secret_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ConfigurationError, "TYPESAFE_API_KEY"):
                    load_api_key(Path(directory))


if __name__ == "__main__":
    unittest.main()
