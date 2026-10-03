"""Exercise the production text CLI across fake HTTP and desktop boundaries."""

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
    """Report a fixed installed-app catalog without launching anything."""

    def URLForApplicationWithBundleIdentifier_(self, bundle_id: str) -> object | None:
        return object() if bundle_id in {SAFARI.bundle_id, TERMINAL.bundle_id, FINDER.bundle_id} else None


class FakeDesktop(MacDesktop):
    """Use production candidate generation with controlled observations and effects."""

    def __init__(self, *, drift: bool = False, effect_visible: bool = True) -> None:
        self.workspace = FakeWorkspace()
        self.active = SAFARI
        self.drift = drift
        self.effect_visible = effect_visible
        self.observations = 0
        self.effects = 0

    def snapshot(self) -> DesktopState:
        self.observations += 1
        if self.drift and self.observations == 2:
            self.active = FINDER
        return DesktopState(self.active, (SAFARI, TERMINAL, FINDER), True, True)

    def execute(self, action: object) -> None:
        self.effects += 1
        if self.effect_visible:
            self.active = TERMINAL


class FakeJev:
    """Return one SDK-shaped Choice response for the supplied local candidates."""

    def __init__(self, *, confidence: float = 0.98, choice: str = "action_0") -> None:
        self.confidence = confidence
        self.choice = choice
        self.calls = 0

    def system_one(self, state: object, questions: dict[str, object], *, model: str | None = None) -> object:
        self.calls += 1
        ids = tuple(questions["next_action"].criteria)
        probabilities = {item: 1.0 if item == self.choice else 0.0 for item in ids}
        return SimpleNamespace(
            choices={"next_action": SimpleNamespace(choice=self.choice, probabilities=probabilities, confidence=self.confidence)},
            model="fake-jev",
            request_id="offline-request",
            usage=SimpleNamespace(input_tokens=4, output_tokens=1),
        )


class PythonCLIPipelineTests(unittest.TestCase):
    """Leave one repeatable JSON artifact for success and failure paths."""

    def test_text_goal_to_decision_effect_and_verification(self) -> None:
        cases: dict[str, dict[str, object]] = {}
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"

            def run(name: str, desktop: FakeDesktop, client: FakeJev, *options: str) -> dict[str, object]:
                output = io.StringIO()
                main(["Switch", "to", "Terminal.", *options, "--report", str(report_path)], desktop=desktop, client=client, output=output)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(json.loads(output.getvalue()), report)
                cases[name] = {"outcome": report["outcome"], "effect_count": desktop.effects, "provider_calls": client.calls}
                return report

            happy = FakeDesktop()
            report = run("verified_focus", happy, FakeJev())
            self.assertEqual(report["outcome"], "completed")
            self.assertTrue(report["verified"])
            self.assertEqual(happy.effects, 1)

            stale = FakeDesktop(drift=True)
            report = run("changed_foreground", stale, FakeJev())
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(stale.effects, 0)

            invented = FakeDesktop()
            report = run("unknown_jev_id", invented, FakeJev(choice="invented"))
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(invented.effects, 0)

            cautious = FakeDesktop()
            report = run("low_confidence", cautious, FakeJev(confidence=0.4))
            self.assertEqual(report["outcome"], "rejected")
            self.assertEqual(cautious.effects, 0)

            unseen = FakeDesktop(effect_visible=False)
            report = run("unverified_effect", unseen, FakeJev())
            self.assertEqual(report["outcome"], "unverified")
            self.assertEqual(unseen.effects, 1)

            dry = FakeDesktop()
            report = run("dry_run", dry, FakeJev(), "--dry-run")
            self.assertEqual(report["outcome"], "dry_run")
            self.assertEqual(dry.effects, 0)

            blocked = FakeDesktop()
            client = FakeJev()
            main(["Delete", "passwords", "in", "Terminal", "--report", str(report_path)], desktop=blocked, client=client, output=io.StringIO())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["outcome"], "blocked")
            self.assertEqual(client.calls, 0)
            cases["blocked_before_jev"] = {"outcome": report["outcome"], "effect_count": blocked.effects, "provider_calls": client.calls}

        artifact = Path(__file__).resolve().parents[1] / ".build/python-e2e.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps({"schema_version": 1, "scenarios": cases}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
