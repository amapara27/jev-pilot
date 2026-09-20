"""Offline tests for Jev request construction and closed-set response validation."""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from typing import Any

from jev_lab.client import JevCallError, QUESTION_ID, build_jev_state, select_action
from jev_lab.models import ActionCandidate


class FakeClient:
    """Records one SDK-shaped call and returns a supplied response."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[Any, Any, str | None]] = []

    def system_one(
        self, state: Any, questions: Any, *, model: str | None = None
    ) -> Any:
        # Capture the exact outbound contract while keeping every test offline.
        self.calls.append((state, questions, model))
        return self.response


def candidates() -> tuple[ActionCandidate, ...]:
    """Return a compact deterministic candidate set for adapter tests."""

    return (
        ActionCandidate(
            id="action_0",
            kind="FOCUS_APP",
            parameters={"bundle_identifier": "com.apple.Terminal", "name": "Terminal"},
            description="Bring Terminal to the foreground.",
        ),
        ActionCandidate(
            id="action_1",
            kind="STOP",
            parameters={"reason": "goal complete"},
            description="Stop when the goal is complete.",
        ),
    )


def response(
    *,
    choice: str = "action_0",
    probabilities: dict[str, float] | None = None,
    confidence: float = 0.8,
) -> Any:
    """Build the SDK response surface consumed by the adapter."""

    answer = SimpleNamespace(
        choice=choice,
        probabilities=probabilities or {"action_0": 0.8, "action_1": 0.2},
        confidence=confidence,
    )
    return SimpleNamespace(
        choices={QUESTION_ID: answer},
        model="jev-1.13.0",
        request_id="request-test",
        usage=SimpleNamespace(input_tokens=123, output_tokens=17),
    )


class JevClientTests(unittest.TestCase):
    """Verifies the provider never escapes the locally supplied candidates."""

    def test_formats_state_and_exact_choice_criteria(self) -> None:
        fake = FakeClient(response())

        decision = select_action(
            "  Switch to Terminal.  ",
            {"active_application": {"name": "Safari"}},
            candidates(),
            client=fake,
        )

        self.assertEqual(decision.selected_candidate.id, "action_0")
        self.assertEqual(decision.model, "jev-1.13.0")
        self.assertEqual(decision.request_id, "request-test")
        self.assertEqual(decision.input_tokens, 123)
        self.assertEqual(decision.output_tokens, 17)
        self.assertEqual(len(fake.calls), 1)
        state, questions, model = fake.calls[0]
        self.assertEqual(
            state,
            {
                "transcription": "Switch to Terminal.",
                "desktop_state": {"active_application": {"name": "Safari"}},
            },
        )
        self.assertEqual(model, "jev-latest")
        choice_question = questions[QUESTION_ID]
        self.assertEqual(set(choice_question.criteria), {"action_0", "action_1"})
        self.assertEqual(choice_question.criteria["action_0"]["action"], "FOCUS_APP")
        self.assertEqual(
            choice_question.criteria["action_0"]["parameters"]["bundle_identifier"],
            "com.apple.Terminal",
        )

    def test_rejects_empty_transcription_and_candidates(self) -> None:
        with self.assertRaisesRegex(JevCallError, "transcription"):
            build_jev_state("   ", {})
        with self.assertRaisesRegex(JevCallError, "At least one"):
            select_action("Do something", {}, (), client=FakeClient(response()))

    def test_rejects_duplicate_candidate_ids(self) -> None:
        duplicate = (candidates()[0], candidates()[0])
        with self.assertRaisesRegex(JevCallError, "unique"):
            select_action("Switch apps", {}, duplicate, client=FakeClient(response()))

    def test_rejects_unknown_choice(self) -> None:
        with self.assertRaisesRegex(JevCallError, "unknown"):
            select_action(
                "Switch apps", {}, candidates(), client=FakeClient(response(choice="invented"))
            )

    def test_rejects_probability_key_mismatch(self) -> None:
        mismatches = (
            {"action_0": 1.0},
            {"action_0": 0.8, "action_1": 0.1, "invented": 0.1},
        )
        for probabilities in mismatches:
            with self.subTest(probabilities=probabilities):
                with self.assertRaisesRegex(JevCallError, "keys"):
                    select_action(
                        "Switch apps",
                        {},
                        candidates(),
                        client=FakeClient(response(probabilities=probabilities)),
                    )

    def test_rejects_invalid_probability_values(self) -> None:
        cases = (
            {"action_0": math.nan, "action_1": math.nan},
            {"action_0": 1.1, "action_1": -0.1},
            {"action_0": 0.4, "action_1": 0.4},
        )
        for probabilities in cases:
            with self.subTest(probabilities=probabilities):
                with self.assertRaises(JevCallError):
                    select_action(
                        "Switch apps",
                        {},
                        candidates(),
                        client=FakeClient(response(probabilities=probabilities)),
                    )

    def test_rejects_non_maximal_selection(self) -> None:
        with self.assertRaisesRegex(JevCallError, "maximum-probability"):
            select_action(
                "Switch apps",
                {},
                candidates(),
                client=FakeClient(
                    response(
                        choice="action_0",
                        probabilities={"action_0": 0.2, "action_1": 0.8},
                    )
                ),
            )

    def test_rejects_invalid_confidence(self) -> None:
        with self.assertRaisesRegex(JevCallError, "confidence"):
            select_action(
                "Switch apps",
                {},
                candidates(),
                client=FakeClient(response(confidence=1.2)),
            )

    def test_requires_api_key_for_owned_client(self) -> None:
        with self.assertRaisesRegex(JevCallError, "API key"):
            select_action("Switch apps", {}, candidates())


if __name__ == "__main__":
    unittest.main()
