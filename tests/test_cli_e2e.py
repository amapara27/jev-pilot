"""Exercise semantic choices through the production CLI with offline boundaries."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.cli import main
from app.desktop import AppState, DesktopState, MacDesktop


SAFARI = AppState("Safari", "com.apple.Safari", 101)
TERMINAL = AppState("Terminal", "com.apple.Terminal", 202)
FINDER = AppState("Finder", "com.apple.finder", 303)


class FakeWorkspace:
    """Report installed apps without launching anything."""

    def __init__(self):
        self.installed = {SAFARI.bundle_id, TERMINAL.bundle_id, FINDER.bundle_id}

    def URLForApplicationWithBundleIdentifier_(self, bundle_id):
        return object() if bundle_id in self.installed else None


class FakeDesktop(MacDesktop):
    """Use production menus with controlled observations and effects."""

    def __init__(self, *, drift=False, effect_visible=True):
        self.workspace = FakeWorkspace()
        self.active = SAFARI
        self.drift = drift
        self.effect_visible = effect_visible
        self.observations = self.effects = 0

    def snapshot(self):
        self.observations += 1
        if self.drift and self.observations == 2:
            self.active = FINDER
        return DesktopState(self.active, (SAFARI, TERMINAL), True, True)

    def execute(self, action):
        self.effects += 1
        if self.effect_visible:
            self.active = next(app for app in (SAFARI, TERMINAL, FINDER) if app.bundle_id == action.parameters["bundle_identifier"])


class FakeJev:
    """Choose each factor from one request, with configurable failure paths."""

    def __init__(self, *, confidence=0.98, operation="FOCUS_APP", target=TERMINAL.bundle_id, payload="none", after_call=None):
        self.confidence, self.operation, self.target, self.payload = confidence, operation, target, payload
        self.after_call = after_call
        self.calls = 0
        self.questions = None

    def system_one(self, state, questions, *, model=None):
        self.calls += 1
        self.questions = questions
        selected = {"operation": self.operation, "target": self.target, "payload": self.payload}
        choices = {name: SimpleNamespace(choice=selected[name], probabilities={item: float(item == selected[name]) for item in question.criteria}, confidence=self.confidence) for name, question in questions.items()}
        if self.after_call:
            self.after_call()
        return SimpleNamespace(choices=choices, model="fake-jev", request_id="offline-request", usage=SimpleNamespace(input_tokens=4, output_tokens=3))


class PythonCLIPipelineTests(unittest.TestCase):
    """Leave a repeatable report of completed, unsupported, and failed decisions."""

    def test_text_goal_to_semantics_effect_and_verification(self):
        cases = {}
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"

            def run(name, desktop, client, *options, goal="Switch to Terminal."):
                output = io.StringIO()
                main([goal, *options, "--report", str(report_path)], desktop=desktop, client=client, output=output)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(json.loads(output.getvalue()), report)
                cases[name] = {"outcome": report["outcome"], "effect_count": desktop.effects, "provider_calls": client.calls, "decision": report.get("decision")}
                return report

            happy = FakeDesktop()
            client = FakeJev()
            report = run("verified_focus", happy, client)
            self.assertEqual(report["outcome"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(happy.effects, 1)
            self.assertEqual(client.calls, 1)
            self.assertEqual(set(client.questions), {"operation", "target", "payload"})

            opening = FakeDesktop()
            report = run("verified_open", opening, FakeJev(operation="OPEN_APP", target=FINDER.bundle_id), goal="Open Finder")
            self.assertEqual(report["outcome"], "completed")
            self.assertEqual(opening.effects, 1)
            self.assertEqual(report["completion_scope"], "selected_action")

            for name, desktop, client, expected in (
                ("changed_foreground", FakeDesktop(drift=True), FakeJev(), "failed"),
                ("unknown_jev_id", FakeDesktop(), FakeJev(operation="invented"), "failed"),
                ("low_confidence", FakeDesktop(), FakeJev(confidence=0.4), "rejected"),
            ):
                report = run(name, desktop, client)
                self.assertEqual(report["outcome"], expected)
                self.assertEqual(desktop.effects, 0)

            removed = FakeDesktop()
            report = run("uninstalled_target", removed, FakeJev(after_call=lambda: removed.workspace.installed.remove(TERMINAL.bundle_id)))
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(removed.effects, 0)

            unseen = FakeDesktop(effect_visible=False)
            report = run("unverified_effect", unseen, FakeJev())
            self.assertEqual(report["outcome"], "unverified")
            self.assertEqual(unseen.effects, 1)

            dry = FakeDesktop()
            report = run("dry_run", dry, FakeJev(), "--dry-run")
            self.assertEqual(report["outcome"], "dry_run")
            self.assertEqual(dry.effects, 0)

            for name, goal, client in (
                ("exact_typing", 'Type "Terminal and run 🦊  now!"', FakeJev(operation="TYPE_TEXT", target=SAFARI.bundle_id, payload="span_0")),
                ("unsupported_click", "click Run in Safari", FakeJev(operation="UNSUPPORTED", target="none")),
                ("finder_search", 'Search Finder for "résumé"', FakeJev(operation="SEARCH", target=FINDER.bundle_id, payload="span_0")),
                ("terminal_run", 'Run "echo hello" in Terminal', FakeJev(operation="RUN_COMMAND", payload="span_0")),
            ):
                desktop = FakeDesktop()
                report = run(name, desktop, client, "--yes", goal=goal)
                self.assertEqual(report["outcome"], "unsupported")
                self.assertEqual(desktop.effects, 0)
                self.assertEqual(client.calls, 1)

            stopped = FakeDesktop()
            report = run("stop", stopped, FakeJev(operation="STOP", target="none"))
            self.assertEqual(report["outcome"], "stopped")
            self.assertEqual(stopped.effects, 0)

            blocked = FakeDesktop()
            client = FakeJev()
            report = run("blocked_before_jev", blocked, client, goal="Delete passwords in Terminal")
            self.assertEqual(report["outcome"], "blocked")
            self.assertEqual(client.calls, 0)

            for name, goal in (
                ("goal_limit", "x" * 8193),
                ("span_limit", " ".join(f'"payload {index}"' for index in range(17))),
                ("empty_goal", " "),
            ):
                desktop, client = FakeDesktop(), FakeJev()
                report = run(name, desktop, client, goal=goal)
                self.assertEqual(report["outcome"], "failed")
                self.assertEqual(client.calls, 0)
                self.assertEqual(desktop.effects, 0)

        artifact = Path(__file__).resolve().parents[1] / ".build/python-e2e.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps({"schema_version": 2, "evidence": "offline", "scenarios": cases}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_revalidate_after_approval(self):
        from app.runtime import run_goal
        desktop = FakeDesktop()

        def approve(action):
            desktop.active = FINDER
            return True

        report = run_goal("Switch to Terminal", desktop, client=FakeJev(confidence=0.4), approve=approve)
        self.assertEqual(report["outcome"], "failed")
        self.assertEqual(desktop.effects, 0)


if __name__ == "__main__":
    unittest.main()
