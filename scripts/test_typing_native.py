"""Verify native typing in a disposable TextEdit document, with optional live Jev."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from types import SimpleNamespace

from app.cli import _save_report
from app.desktop import MacDesktop
from app.runtime import run_goal
from app.text_input import TextInputError


class FixtureDesktop(MacDesktop):
    """Allow acceptance effects only on the uniquely identified disposable editor."""

    fixture_identifier: str | None = None

    def validate_action(self, action, before, fresh):
        if action.kind != "TYPE_TEXT" or action.parameters.get("element_id") != self.fixture_identifier:
            raise TextInputError("Native acceptance may only type into its disposable fixture.")
        super().validate_action(action, before, fresh)


class FixtureChoice:
    """Select only the unique fixture editor; production code validates and executes."""

    def __init__(self, identifier: str):
        self.identifier = identifier

    def system_one(self, state, questions, *, model=None):
        selected = {"operation": "TYPE_TEXT", "target": self.identifier, "payload": "span_0"}
        answers = {
            name: SimpleNamespace(choice=selected[name], confidence=1.0,
                                  probabilities={key: float(key == selected[name]) for key in question.criteria})
            for name, question in questions.items()
        }
        return SimpleNamespace(choices=answers, model="scripted-native-fixture", usage=None, request_id=None)


def main() -> int:
    """Fail on permissions/readiness rather than skipping native acceptance."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-jev", action="store_true", help="Send the fixture goal and bounded native target metadata to TypeSafe.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / ".build/typing-native.json"
    desktop = FixtureDesktop()
    report = {"outcome": "failed", "effect_sent": False, "verified": False,
              "evidence": "native", "provider": "live Jev" if args.live_jev else "scripted fixture",
              "python_executable": sys.executable}
    try:
        if not desktop.text.ax.AXIsProcessTrusted():
            raise RuntimeError("Grant Accessibility to this Python/terminal host, then rerun native acceptance.")
        output.parent.mkdir(parents=True, exist_ok=True)
        fixture_dir = Path(tempfile.mkdtemp(prefix="jev-typing-", dir=output.parent))
        document = fixture_dir / "Jev disposable typing fixture.txt"
        initial = f"Jev disposable document {fixture_dir.name}\n"
        document.write_text(initial, encoding="utf-8")
        report["fixture"] = str(document)
        subprocess.run(["open", "-a", "TextEdit", str(document)], check=True)
        deadline = monotonic() + 8
        target = None
        while monotonic() < deadline:
            state = desktop.snapshot()
            if state.active and state.active.bundle_id == "com.apple.TextEdit":
                # Match both fixture window title and exact disposable content before
                # allowing any text effect. Never type into an existing user document.
                fields = [item for item in state.text_fields if item.can_type and item.value == initial
                          and document.name in str(desktop.text.read(item.window, "AXTitle") or "")]
                if len(fields) == 1:
                    target = fields[0]
                    break
            sleep(0.05)
        if target is None:
            raise RuntimeError("The uniquely labeled TextEdit fixture did not expose a focused, verifiable editor.")
        desktop.fixture_identifier = target.id
        result = run_goal('type "Jev native Unicode check 🦊 café"', desktop,
                          client=None if args.live_jev else FixtureChoice(target.id))
        report.update(result)
    except Exception as error:
        report["reason"] = str(error) if isinstance(error, RuntimeError) else f"Native acceptance failed ({type(error).__name__})."
    _save_report(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["outcome"] == "completed" and report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
