"""Check all three closed-set answers before local semantic resolution."""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from app.decision import JevCallError, select_action
from app.desktop import AppState, DesktopState, MacDesktop
from app.semantics import SemanticError


class Workspace:
    """Ground just Safari and Terminal as installed applications."""

    def URLForApplicationWithBundleIdentifier_(self, bundle_id):
        return object() if bundle_id in {"com.apple.Safari", "com.apple.Terminal"} else None


class Client:
    """Build SDK-shaped responses from the exact outgoing factor menus."""

    def __init__(self, *, operation="FOCUS_APP", target="com.apple.Terminal", payload="none", mutate=None):
        self.operation, self.target, self.payload = operation, target, payload
        self.mutate = mutate
        self.calls = []

    def system_one(self, state, questions, *, model=None):
        self.calls.append((state, questions, model))
        selected = {"operation": self.operation, "target": self.target, "payload": self.payload}
        choices = {}
        for name, question in questions.items():
            key = selected[name]
            choices[name] = SimpleNamespace(choice=key, probabilities={item: float(item == key) for item in question.criteria}, confidence=0.9)
        if self.mutate:
            self.mutate(choices)
        return SimpleNamespace(choices=choices, model="offline", request_id="test", usage=SimpleNamespace(input_tokens=10, output_tokens=3))


class SemanticDecisionTests(unittest.TestCase):
    """Enumerated trust-boundary failures apply to each independent factor."""

    def setUp(self):
        self.desktop = MacDesktop.__new__(MacDesktop)
        self.desktop.workspace = Workspace()
        self.state = DesktopState(AppState("Safari", "com.apple.Safari", 1), (AppState("Safari", "com.apple.Safari", 1), AppState("Terminal", "com.apple.Terminal", 2)), True, True)

    def choose(self, client, goal="Switch to Terminal"):
        return select_action(goal, self.state.provider_state(), self.desktop.semantic_menu(goal, self.state), client=client)

    def test_single_request_contains_all_operations_targets_and_exact_spans(self):
        client = Client(operation="TYPE_TEXT", target="com.apple.Safari", payload="span_0")
        decision = self.choose(client, 'Type "Terminal and run 🦊  now!"')
        self.assertIsNone(decision.selected_candidate)
        self.assertEqual(decision.semantic["payload"]["text"], "Terminal and run 🦊  now!")
        self.assertEqual(len(client.calls), 1)
        state, questions, _ = client.calls[0]
        self.assertEqual(state["semantic_options"]["payloads"], self.desktop.semantic_menu('Type "Terminal and run 🦊  now!"', self.state).payloads)
        self.assertEqual(set(questions), {"operation", "target", "payload"})
        self.assertIn("RUN_COMMAND", questions["operation"].criteria)
        self.assertIn("UNSUPPORTED", questions["operation"].criteria)
        self.assertIn("com.apple.Safari", questions["target"].criteria)

    def test_each_factor_rejects_bad_answers(self):
        failures = {
            "unknown": lambda a: setattr(a, "choice", "invented"),
            "keys": lambda a: setattr(a, "probabilities", {"invented": 1.0}),
            "nan": lambda a: setattr(a, "probabilities", dict.fromkeys(a.probabilities, math.nan)),
            "boolean": lambda a: setattr(a, "probabilities", dict.fromkeys(a.probabilities, True)),
            "range": lambda a: setattr(a, "probabilities", dict.fromkeys(a.probabilities, -1)),
            "total": lambda a: setattr(a, "probabilities", dict.fromkeys(a.probabilities, 0.0)),
            "confidence": lambda a: setattr(a, "confidence", math.inf),
            "winner": lambda a: setattr(a, "choice", next(k for k in a.probabilities if k != a.choice)),
        }
        for factor in ("operation", "target", "payload"):
            for name, failure in failures.items():
                with self.subTest(factor=factor, failure=name), self.assertRaises(JevCallError):
                    self.choose(Client(mutate=lambda choices: failure(choices[factor])), 'Switch to Terminal, not "hello"')
            with self.subTest(factor=factor, failure="missing"), self.assertRaises(JevCallError):
                self.choose(Client(mutate=lambda choices: choices.pop(factor)))

    def test_incompatible_factors_never_resolve_to_action(self):
        for operation, target, payload in (
            ("STOP", "com.apple.Terminal", "none"),
            ("FOCUS_APP", "com.apple.Terminal", "span_0"),
            ("OPEN_APP", "com.apple.Terminal", "none"),
            ("FOCUS_APP", "com.apple.Safari", "none"),
            ("TYPE_TEXT", "none", "none"),
            ("CREATE_NOTE", "com.apple.Terminal", "none"),
            ("RUN_COMMAND", "com.apple.Safari", "span_0"),
        ):
            with self.subTest(operation=operation, target=target, payload=payload), self.assertRaises(JevCallError):
                self.choose(Client(operation=operation, target=target, payload=payload), 'Type "hello"')

    def test_minimum_confidence_and_local_action(self):
        client = Client(mutate=lambda answers: setattr(answers["target"], "confidence", 0.3))
        decision = self.choose(client)
        self.assertEqual(decision.confidence, 0.3)
        self.assertEqual(decision.selected_candidate.kind, "FOCUS_APP")
        self.assertEqual(decision.selected_candidate.parameters["bundle_identifier"], "com.apple.Terminal")

    def test_exact_unquoted_alternatives_and_bounds(self):
        goal = "type café 🦊 and run later in Terminal"
        menu = self.desktop.semantic_menu(goal, self.state)
        texts = [value["text"] for key, value in menu.payloads.items() if key != "none"]
        self.assertIn("café 🦊 and run later in Terminal", texts)
        self.assertIn("café 🦊 and run later", texts)
        for key, span in menu.payloads.items():
            if key != "none":
                self.assertEqual(goal[span["start"]:span["end"]], span["text"])
        with self.assertRaisesRegex(SemanticError, "limit"):
            self.desktop.semantic_menu("x" * 8193, self.state)

    def test_empty_goal_and_missing_key(self):
        with self.assertRaises(SemanticError):
            self.choose(Client(), " ")
        with self.assertRaisesRegex(JevCallError, "API key"):
            select_action("Switch to Terminal", {}, self.desktop.semantic_menu("Switch to Terminal", self.state))


if __name__ == "__main__":
    unittest.main()
