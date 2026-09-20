"""Tests fixture discovery and the human-plus-JSON output contract."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from jev_lab.cli import main, print_result
from jev_lab.config import ConfigurationError
from jev_lab.fixtures import SCENARIOS
from jev_lab.models import JevDecision


class CLITests(unittest.TestCase):
    """Verifies CLI-only behavior without making provider requests."""

    def test_lists_every_fixture_without_api_key(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()

        exit_code = main(["--list"], output=output, error_output=errors)

        self.assertEqual(exit_code, 0)
        self.assertEqual(errors.getvalue(), "")
        for name in SCENARIOS:
            self.assertIn(name, output.getvalue())

    def test_prints_readable_summary_and_machine_readable_json(self) -> None:
        scenario = SCENARIOS["focus-terminal"]
        decision = JevDecision(
            selected_candidate=scenario.candidates[0],
            probabilities={"action_0": 0.9, "action_1": 0.1},
            confidence=0.85,
            model="jev-1.13.0",
            request_id="request-test",
            input_tokens=200,
            output_tokens=20,
            latency_milliseconds=125,
        )
        output = io.StringIO()

        print_result(scenario, decision, output)

        rendered = output.getvalue()
        self.assertIn("Selected: action_0: FOCUS_APP", rendered)
        self.assertIn("action_0: 0.900000 *", rendered)
        json_text = rendered.split("JSON result:\n", maxsplit=1)[1]
        payload = json.loads(json_text)
        self.assertEqual(payload["fixture"], "focus-terminal")
        self.assertEqual(
            payload["decision"]["selected_candidate"]["id"], "action_0"
        )

    def test_fixture_run_reports_missing_key_without_calling_provider(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()

        with patch(
            "jev_lab.cli.load_api_key",
            side_effect=ConfigurationError("TYPESAFE_API_KEY is missing."),
        ):
            exit_code = main(
                ["--fixture", "focus-terminal"],
                output=output,
                error_output=errors,
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "Error: TYPESAFE_API_KEY is missing.\n")


if __name__ == "__main__":
    unittest.main()
