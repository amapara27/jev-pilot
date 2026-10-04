"""Test a full CLI activation/focus/typing command against disposable TextEdit data."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

from app.cli import _save_report, main as run_cli
from app.desktop import DesktopState, MacDesktop
from app.semantics import SemanticMenu
from app.text_input import TextInputError


TEXTEDIT = "com.apple.TextEdit"
GOAL = 'open TextEdit and type "Jev native Unicode check 🦊 café"'


def readiness(state) -> dict:
    """Expose field capabilities and rejection facts without document contents."""

    return {"focused_window_available": state.focused_window_available,
            "field_count": len(state.text_fields), "can_type_count": sum(item.can_type for item in state.text_fields),
            **state.editor_diagnostics}


def inspect_application(desktop, name):
    """Read an existing app's focused window without activating it or sending data."""

    if name == "frontmost":
        return desktop.snapshot()
    bundle = {"Notes": "com.apple.Notes", "TextEdit": TEXTEDIT}[name]
    apps = desktop.workspace.runningApplications()
    native = next((app for app in apps if app.bundleIdentifier() == bundle), None)
    if native is None:
        raise RuntimeError(f"{name} is not running; open its document before inspecting.")
    active = desktop._app(native)
    ax = desktop.text.ax
    trusted = bool(ax.AXIsProcessTrusted())
    window, fields, diagnostics = None, (), {}
    if trusted:
        application = ax.AXUIElementCreateApplication(active.pid)
        ax.AXUIElementSetMessagingTimeout(application, 0.2)
        error, window = ax.AXUIElementCopyAttributeValue(application, "AXFocusedWindow", None)
        diagnostics["focused_window_ax_error"] = int(error)
        if error:
            window = None
        if window is not None:
            fields = desktop.text.scan(active.pid, bundle, application, window)
            diagnostics.update(desktop.text.diagnostics)
    return DesktopState(active, tuple(desktop._app(app) for app in apps if app.bundleIdentifier()),
                        trusted, window is not None, fields, diagnostics)


class FixtureDesktop(MacDesktop):
    """Allow activation plus focus/insertion only in the unique disposable document."""

    document: Path | None = None
    initial: str | None = None

    def fixture_window(self, window) -> bool:
        """Match the unique fixture title and its file URL whenever AX supplies it."""

        title = str(self.text.read(window, "AXTitle") or "")
        if self.document is None or self.document.stem not in title:
            return False
        url = self.text.read(window, "AXDocument")
        return url is None or unquote(urlparse(str(url)).path) == str(self.document)

    def semantic_menu(self, goal, state):
        """Keep app selection real while withholding fields outside the test fixture."""

        menu = super().semantic_menu(goal, state)
        fixture_ids = {item.id for item in state.text_fields
                       if item.bundle_id == TEXTEDIT and item.role == "AXTextArea" and self.fixture_window(item.window)}
        return SemanticMenu(goal, {key: facts for key, facts in menu.targets.items()
                                   if facts.get("scope") != "field" or key in fixture_ids}, menu.payloads)

    def validate_action(self, action, before, fresh):
        if action.parameters.get("bundle_identifier") != TEXTEDIT:
            raise TextInputError("Native acceptance may only activate TextEdit and edit its disposable fixture.")
        if action.kind in {"TYPE_TEXT", "FOCUS_FIELD"}:
            fields = [item for item in fresh.text_fields if item.id == action.parameters.get("element_id")
                      and item.role == "AXTextArea" and self.fixture_window(item.window)]
            if len(fields) != 1 or (action.kind == "TYPE_TEXT" and fields[0].value != self.initial):
                raise TextInputError("Native acceptance may only edit its unchanged disposable fixture.")
        super().validate_action(action, before, fresh)


class FixtureChoice:
    """Script each necessary action from fresh menus; do not preselect an editor."""

    def system_one(self, state, questions, *, model=None):
        targets = questions["target"].criteria
        app = targets.get(TEXTEDIT)
        selected = {"operation": "STOP", "target": "none", "payload": "none"}
        if app and not app["active"]:
            selected = {"operation": "FOCUS_APP_FOR_TEXT" if app["running"] else "OPEN_APP_FOR_TEXT",
                        "target": TEXTEDIT, "payload": "span_0"}
        else:
            fields = [(key, facts) for key, facts in targets.items() if facts.get("scope") == "field"]
            if len(fields) == 1:
                key, facts = fields[0]
                if facts["can_type"]:
                    selected = {"operation": "TYPE_TEXT", "target": key, "payload": "span_0"}
                elif facts["focusable"] and not facts["focused"]:
                    selected = {"operation": "FOCUS_FIELD", "target": key, "payload": "span_0"}
        answers = {
            name: SimpleNamespace(choice=selected[name], confidence=1.0,
                                  probabilities={key: float(key == selected[name]) for key in question.criteria})
            for name, question in questions.items()
        }
        return SimpleNamespace(choices=answers, model="scripted-native-fixture", usage=None, request_id=None)


def main(argv=None, *, desktop=None, output=sys.stdout) -> int:
    """Require verified activation and insertion through the production command CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-jev", action="store_true", help="Send the fixture goal and bounded native target metadata to TypeSafe.")
    parser.add_argument("--inspect", nargs="?", const="frontmost", choices=("frontmost", "Notes", "TextEdit"),
                        help="Read an existing app's editor facts without activation, effects or provider calls.")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--report", type=Path, default=root / ".build/typing-native.json")
    args = parser.parse_args(argv)
    desktop = desktop if desktop is not None else FixtureDesktop()
    if args.inspect:
        state = inspect_application(desktop, args.inspect)
        print(json.dumps({"accessibility_trusted": state.ax_trusted,
                          "observed_app": state.active.name if state.active else None,
                          "editor_readiness": readiness(state), "effect_sent": False,
                          "fields": [{key: value for key, value in item.provider_target().items() if key != "label"}
                                     for item in state.text_fields]}, indent=2), file=output)
        return 0 if state.ax_trusted else 1
    report = {"outcome": "failed", "effect_sent": False, "verified": False,
              "workflow_verified": False, "goal": GOAL,
              "evidence": "native", "provider": "live Jev" if args.live_jev else "scripted fixture",
              "python_executable": sys.executable}
    try:
        if not desktop.text.ax.AXIsProcessTrusted():
            raise RuntimeError("Grant Accessibility to this Python/terminal host, then rerun native acceptance.")
        starting = desktop.snapshot().active
        if starting is None or starting.bundle_id == TEXTEDIT:
            raise RuntimeError("Run native command acceptance from Terminal with TextEdit in the background.")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        fixture_dir = Path(tempfile.mkdtemp(prefix="jev-typing-", dir=args.report.parent)).resolve()
        document = fixture_dir / f"Jev disposable typing fixture {fixture_dir.name}.txt"
        initial = f"Jev disposable document {fixture_dir.name}\n"
        document.write_text(initial, encoding="utf-8")
        desktop.document, desktop.initial = document, initial
        report["fixture"] = str(document)
        # Fixture setup opens test data, then restores the starting app. The CLI
        # itself must activate TextEdit, choose/focus its field, and insert text.
        subprocess.run(["open", "-a", "TextEdit", str(document)], check=True)
        deadline = monotonic() + 8
        while monotonic() < deadline:
            state = desktop.snapshot()
            report["editor_readiness"] = readiness(state)
            if state.active and state.active.bundle_id == TEXTEDIT:
                app = desktop.text.ax.AXUIElementCreateApplication(state.active.pid)
                window = desktop.text.read(app, "AXFocusedWindow")
                if window is not None and document.stem in str(desktop.text.read(window, "AXTitle") or ""):
                    break
            sleep(0.05)
        else:
            raise RuntimeError("TextEdit did not expose the disposable fixture window; see editor_readiness.")
        subprocess.run(["open", "-b", starting.bundle_id], check=True)
        deadline = monotonic() + 8
        while monotonic() < deadline:
            if desktop.snapshot().active == starting:
                break
            sleep(0.05)
        else:
            raise RuntimeError("Could not restore the starting app before testing the command.")
        run_cli([GOAL, "--report", str(args.report)], desktop=desktop,
                client=None if args.live_jev else FixtureChoice(), output=io.StringIO())
        report.update(json.loads(args.report.read_text()))
        report["workflow_verified"] = bool(
            report["outcome"] == "completed" and report["verified"] and report.get("completion_scope") == "single_insertion"
            and any(step["verified"] and step["decision"]["semantic"]["operation"].endswith("_APP_FOR_TEXT")
                    for step in report["steps"]))
        if report["outcome"] == "completed" and not report["workflow_verified"]:
            report.update(outcome="unverified", verified=False, reason="The full activation-to-insertion command was not verified.")
    except Exception as error:
        report["reason"] = str(error) if isinstance(error, RuntimeError) else f"Native acceptance failed ({type(error).__name__})."
    _save_report(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True), file=output)
    return 0 if report["workflow_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
