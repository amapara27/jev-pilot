"""Exercise Notes/Finder workflows through real menus, guards, dispatch and verification."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from threading import Event
from unittest.mock import patch

from app.desktop import MacDesktop
from app.runtime import run_goal
from app.text_input import NativeText
from test_typing_e2e import NativeAX, NativeQuartz, Node, Workspace, Jev
from app.cli import main


class WorkflowAX(NativeAX):
    """Fake native effects while retaining the production workflow scanner and executor."""

    def __init__(self, bundle, url=None):
        super().__init__(bundle=bundle, value="existing note")
        title = "New Note" if bundle == "com.apple.Notes" else "Find"
        self.control = Node(AXRole="AXMenuItem", AXTitle=title, AXEnabled=True)
        self.open_control = Node(AXRole="AXMenuItem", AXTitle="Open", AXEnabled=True)
        self.application.attrs["AXMenuBar"] = Node(AXRole="AXMenuBar", AXChildren=[self.control, self.open_control])
        self.effects = []
        self.url = url
        self.ignore_effect = False
        if bundle == "com.apple.finder":
            self.editor.attrs.update(AXRole="AXTextField", AXSubrole="AXSearchField", AXDescription="Search", AXValue="old query")
            self.item = Node(AXRole="AXRow", AXTitle="resume.txt", AXURL=url, AXSelected=False, AXEnabled=True)
            self.item.writable.add("AXSelected")
            self.collection = Node(AXRole="AXOutline", AXSelectedRows=[], AXChildren=[self.item], AXParent=self.window)
            self.item.attrs["AXParent"] = self.collection
            self.window.attrs["AXChildren"].append(self.collection)

    def AXUIElementCopyActionNames(self, node, unused):
        return 0, ["AXPress"] if node in (self.control, self.open_control) else []

    def AXUIElementPerformAction(self, node, action):
        self.effects.append(("press", node.attrs["AXTitle"]))
        if not self.ignore_effect and node is self.control and self.bundle == "com.apple.Notes":
            self.editor.attrs.update(AXValue="", AXSelectedTextRange=(0, 0))
        if not self.ignore_effect and node is self.open_control:
            self.window.attrs["AXDocument"] = self.url
        return 0

    def AXUIElementSetAttributeValue(self, node, attribute, value):
        if attribute in {"AXSelected", "AXValue"}:
            self.effects.append((attribute, value))
            if not self.ignore_effect:
                node.attrs[attribute] = value
                if attribute == "AXSelected":
                    self.collection.attrs["AXSelectedRows"] = [node]
            return 0
        return super().AXUIElementSetAttributeValue(node, attribute, value)


def setup(bundle, url=None):
    ax = WorkflowAX(bundle, url)
    workspace = Workspace(ax)
    workspace.URLForApplicationWithBundleIdentifier_ = lambda identifier: object() if identifier in {bundle, "com.apple.TextEdit", "com.apple.Safari"} else None
    desktop = MacDesktop(workspace=workspace, text=NativeText(ax=ax, quartz=NativeQuartz(ax), equal=lambda a, b: a is b))
    return desktop, ax


class WorkflowEndToEnd(unittest.TestCase):
    """Verify exact effects, missing capabilities, duplicate identity and stale targets."""

    @unittest.skipUnless(os.environ.get("JEV_LIVE_JEV") == "1", "Live Jev is explicitly opt-in; native services remain fake.")
    def test_live_jev_progression_with_synthetic_native_services(self):
        """Exercise the production CLI/provider adapter without changing the desktop."""

        cases = {}
        for bundle, command, first_operation in (
            ("com.apple.Notes", 'create a new note in Notes and type "hello world"', "CREATE_NOTE"),
            ("com.apple.TextEdit", 'create a new document in TextEdit and type "hello world"', "PRESS_CONTROL"),
        ):
            desktop, ax = setup(bundle)
            ax.editor.attrs["AXDescription"] = "Note Body Text View" if bundle == "com.apple.Notes" else "Document Body"
            if bundle == "com.apple.TextEdit":
                ax.control.attrs["AXTitle"] = "New Document"
            old_row = Node(AXRole="AXRow", AXSelected=True)
            table = Node(AXRole="AXTable", AXSelectedRows=[old_row], AXChildren=[old_row], AXParent=ax.window)
            old_row.attrs["AXParent"] = table
            ax.window.attrs["AXChildren"].append(table)
            original_press = ax.AXUIElementPerformAction
            def press(node, action):
                result = original_press(node, action)
                if node is ax.control:
                    if bundle == "com.apple.Notes":
                        old_row.attrs["AXSelected"] = False
                        new_row = Node(AXRole="AXRow", AXSelected=True, AXParent=table)
                        table.attrs.update(AXSelectedRows=[new_row], AXChildren=[old_row, new_row])
                    else:
                        editor = Node(**{**ax.editor.attrs, "AXValue": "", "AXSelectedTextRange": (0, 0)})
                        editor.writable = set(ax.editor.writable)
                        ax.editor = editor
                        ax.window.attrs["AXChildren"][0] = editor
                    ax.application.attrs["AXFocusedUIElement"] = ax.window
                return result
            ax.AXUIElementPerformAction = press
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="y"):
                    status = main([command, "--report", str(path)], desktop=desktop, output=io.StringIO())
                report = json.loads(path.read_text())
            cases[bundle] = report
            artifact = Path(__file__).resolve().parents[1] / ".build/live-workflow-e2e.json"
            artifact.write_text(json.dumps({"evidence": "live Jev; synthetic native services only", "scenarios": cases}, indent=2) + "\n")
            self.assertEqual(status, 0, {"outcome": report["outcome"], "reason": report.get("reason")})
            self.assertEqual([item["decision"]["semantic"]["operation"] for item in report["steps"]], [first_operation, "FOCUS_FIELD", "TYPE_TEXT"])
            self.assertTrue(all(item["verified"] for item in report["steps"]))
            self.assertEqual(ax.editor.attrs["AXValue"], "hello world")

    def test_creation_focus_and_insertion_are_separate_steps(self):
        """A reused or delayed new-note body need not be focused to prove creation."""

        from app.verification import verify_action
        cases = {}
        for mode in ("unfocused", "delayed", "replacement", "no_identity", "nonempty", "selection_drift", "executor_selection_drift", "cancel"):
            desktop, ax = setup("com.apple.Notes")
            old_row = Node(AXRole="AXRow", AXSelected=True)
            table = Node(AXRole="AXTable", AXSelectedRows=[old_row], AXChildren=[old_row], AXParent=ax.window)
            old_row.attrs["AXParent"] = table
            ax.window.attrs["AXChildren"].append(table)
            if mode == "no_identity":
                ax.editor.attrs.update(AXValue="", AXSelectedTextRange=(0, 0))
            original_press = ax.AXUIElementPerformAction
            stopping = Event()
            reads = [0]
            def press(node, action):
                result = original_press(node, action)
                ax.application.attrs["AXFocusedUIElement"] = ax.window
                if mode != "no_identity":
                    old_row.attrs["AXSelected"] = False
                    new_row = Node(AXRole="AXRow", AXSelected=True, AXParent=table)
                    table.attrs.update(AXSelectedRows=[new_row], AXChildren=[old_row, new_row])
                if mode == "replacement":
                    old_editor = ax.editor
                    ax.editor = Node(**old_editor.attrs)
                    ax.editor.writable = set(old_editor.writable)
                    ax.window.attrs["AXChildren"][0] = ax.editor
                if mode == "nonempty":
                    ax.editor.attrs.update(AXValue="unexpected existing text", AXSelectedTextRange=(24, 0))
                if mode == "delayed":
                    ax.window.attrs["AXChildren"].remove(ax.editor)
                if mode == "cancel":
                    stopping.set()
                return result
            ax.AXUIElementPerformAction = press
            original_read = ax.AXUIElementCopyAttributeValue
            def read(node, attribute, unused):
                if mode == "delayed" and ax.effects and node is ax.window and attribute == "AXChildren":
                    reads[0] += 1
                    if reads[0] == 3:
                        ax.window.attrs["AXChildren"].insert(0, ax.editor)
                return original_read(node, attribute, unused)
            ax.AXUIElementCopyAttributeValue = read
            class ProgressJev(Jev):
                def system_one(self, state, questions, *, model=None):
                    result = super().system_one(state, questions, model=model)
                    if mode == "selection_drift" and len(self.calls) == 2:
                        table.attrs["AXSelectedRows"] = [old_row]
                        for row in table.attrs["AXChildren"]:
                            row.attrs["AXSelected"] = row is old_row
                    return result
            client = ProgressJev(plan=[("CREATE_NOTE", "com.apple.Notes", "span_0"), ("FOCUS_FIELD", "editor", "span_0"), ("TYPE_TEXT", "editor", "span_0")])
            original_execute = desktop.execute
            def execute(action):
                if mode == "executor_selection_drift" and action.kind == "TYPE_TEXT":
                    table.attrs["AXSelectedRows"] = [old_row]
                    for row in table.attrs["AXChildren"]:
                        row.attrs["AXSelected"] = row is old_row
                return original_execute(action)
            desktop.execute = execute
            with patch("app.runtime.verify_action", side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0.2, **kwargs)):
                report = run_goal('create a new note in Notes and type "hello world"', desktop, client=client, cancel_event=stopping)
            self.assertEqual(sum(effect == ("press", "New Note") for effect in ax.effects), 1)
            self.assertIn("verification", report["steps"][0])
            self.assertNotIn("unexpected existing text", json.dumps(report))
            if mode in {"unfocused", "delayed", "replacement"}:
                self.assertEqual(report["outcome"], "completed", report)
                self.assertEqual(ax.editor.attrs["AXValue"], "hello world")
                self.assertEqual([step["decision"]["semantic"]["operation"] for step in report["steps"]], ["CREATE_NOTE", "FOCUS_FIELD", "TYPE_TEXT"])
                self.assertEqual(client.calls[1]["desktop_state"]["verified_steps"], ["CREATE_NOTE"])
                self.assertEqual(report["steps"][0]["verification"]["reason"], "note_selection_changed")
            else:
                self.assertNotEqual(report["outcome"], "completed")
                self.assertNotIn(("AXSelectedText", "hello world"), ax.effects)
                self.assertEqual(len(client.calls), 1 if mode in {"no_identity", "cancel"} else 2 if mode == "selection_drift" else 3)
                if mode == "no_identity":
                    self.assertEqual(report["steps"][0]["verification"]["reason"], "note_identity_unproven")
                if mode == "cancel":
                    self.assertEqual(report["outcome"], "cancelled")
            cases[mode] = report
        artifact = Path(__file__).resolve().parents[1] / ".build/workflow-progression-e2e.json"
        artifact.write_text(json.dumps({"evidence": "offline native/provider boundaries", "scenarios": cases}, indent=2) + "\n")

    def test_scoped_heads_and_bounded_pre_effect_recovery(self):
        """Only the chosen head can authorize effects; refreshing never inherits approval."""

        cases = {}
        for mode in ("unused_invalid", "chosen_invalid", "replacement", "unstable", "approval_replacement"):
            desktop, ax = setup("com.apple.Notes")
            class ScopedJev(Jev):
                def system_one(self, state, questions, *, model=None):
                    self.assert_scoped(state, questions)
                    result = super().system_one(state, questions, model=model)
                    if mode == "unused_invalid":
                        result.choices["target_STOP"].choice = "invented"
                    if mode == "chosen_invalid":
                        result.choices["target_CREATE_NOTE"].choice = "com.apple.Safari"
                    if mode == "unstable" or (mode == "replacement" and len(self.calls) == 1):
                        replace_control()
                    return result
                def assert_scoped(self, state, questions):
                    self_test.assertLessEqual(set(questions["target_CREATE_NOTE"].criteria), {"com.apple.Notes", "none"})
                    self_test.assertTrue(all(key == "none" or state["semantic_options"]["targets"][key].get("scope") == "field"
                                             for key in questions["target_TYPE_TEXT"].criteria))
            def replace_control():
                ax.control = Node(AXRole="AXMenuItem", AXTitle="New Note", AXEnabled=True)
                ax.application.attrs["AXMenuBar"].attrs["AXChildren"][0] = ax.control
            self_test = self
            plan = [("CREATE_NOTE", "com.apple.Notes", "span_0"), ("TYPE_TEXT", "editor", "span_0")]
            if mode == "replacement":
                plan.insert(0, plan[0])
            if mode == "unstable":
                plan = [plan[0]] * 3
            client = ScopedJev(plan=plan, confidence=0.4 if mode == "approval_replacement" else 1)
            def approve(_):
                replace_control()
                return True
            report = run_goal('create a note and type "hello"', desktop, client=client, approve=approve)
            if mode in {"unused_invalid", "replacement"}:
                self.assertEqual(report["outcome"], "completed", report)
                self.assertEqual(ax.editor.attrs["AXValue"], "hello")
                self.assertEqual(len(ax.effects), 2)
                if mode == "replacement":
                    self.assertEqual(report["steps"][0]["outcome"], "reobserve")
                    self.assertFalse(report["steps"][0]["effect_sent"])
            else:
                self.assertEqual(report["outcome"], "failed", report)
                self.assertEqual(ax.effects, [])
                self.assertLessEqual(len(client.calls), 3)
            cases[mode] = report
        artifact = Path(__file__).resolve().parents[1] / ".build/workflow-recovery-e2e.json"
        artifact.write_text(json.dumps({"evidence": "offline", "scenarios": cases}, indent=2) + "\n")

    def test_generic_app_controls_use_the_same_observe_decide_loop(self):
        """Other accessible apps can create/focus/type, without a Notes-only runner."""

        from app.verification import verify_action
        cases = {}
        for bundle in ("com.apple.TextEdit", "com.example.Editor"):
            for mode in ("new_document", "no_change", "dangerous", "unknown_rejected", "window_drift", "repeat"):
                desktop, ax = setup(bundle)
                ax.control.attrs["AXTitle"] = {"dangerous": "Delete Document", "unknown_rejected": "Perform Unknown Task"}.get(mode, "New Document")
                original_press = ax.AXUIElementPerformAction
                def press(node, action):
                    result = original_press(node, action)
                    if mode in {"new_document", "repeat", "window_drift"}:
                        old_editor = ax.editor
                        ax.editor = Node(**{**old_editor.attrs, "AXValue": "", "AXSelectedTextRange": (0, 0)})
                        ax.editor.writable = set(old_editor.writable)
                        ax.window.attrs["AXChildren"][0] = ax.editor
                        ax.application.attrs["AXFocusedUIElement"] = ax.window
                    return result
                ax.AXUIElementPerformAction = press
                class ControlJev(Jev):
                    def system_one(self, state, questions, *, model=None):
                        if mode == "new_document":
                            self_test.assertNotIn("PRESS_CONTROL" if self.calls else "TYPE_TEXT", questions["operation"].criteria)
                        if len(self.calls) == 1 and mode == "new_document":
                            self_test.assertNotIn("TYPE_TEXT", questions["operation"].criteria)
                            self_test.assertIn("FOCUS_FIELD", questions["operation"].criteria)
                        if not self.calls or mode == "repeat":
                            target = self.first_control if self.calls else next(key for key in questions["target_PRESS_CONTROL"].criteria
                                          if state["semantic_options"]["targets"][key].get("scope") == "control"
                                          and state["semantic_options"]["targets"][key]["label"] == ax.control.attrs["AXTitle"])
                            self.first_control = target
                            self.plan[min(len(self.calls), len(self.plan) - 1)] = ("PRESS_CONTROL", target, "span_0")
                        result = super().system_one(state, questions, model=model)
                        if mode == "window_drift" and len(self.calls) == 2:
                            ax.editor.attrs["AXDescription"] = "different editor"
                        return result
                client = ControlJev(plan=[("STOP", "none", "none"), ("FOCUS_FIELD", "editor", "span_0"), ("TYPE_TEXT", "editor", "span_0")])
                self_test = self
                with patch("app.runtime.verify_action", side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0.1, **kwargs)):
                    report = run_goal('create a new document and type "hello"', desktop, client=client)
                if mode == "new_document":
                    self.assertEqual(report["outcome"], "completed", report)
                    self.assertEqual(ax.editor.attrs["AXValue"], "hello")
                    self.assertEqual([step["decision"]["semantic"]["operation"] for step in report["steps"]], ["PRESS_CONTROL", "FOCUS_FIELD", "TYPE_TEXT"])
                    self.assertEqual(report["steps"][0]["verification"]["reason"], "editor_changed")
                else:
                    self.assertNotEqual(report["outcome"], "completed", report)
                    self.assertNotIn(("AXSelectedText", "hello"), ax.effects)
                    self.assertEqual(len(ax.effects), 0 if mode in {"dangerous", "unknown_rejected"} else 1)
                    if mode == "no_change":
                        self.assertEqual(report["steps"][0]["verification"]["reason"], "control_effect_unobserved")
                    if mode == "dangerous":
                        self.assertEqual(report["outcome"], "blocked")
                    if mode == "unknown_rejected":
                        self.assertEqual(report["outcome"], "rejected")
                    if mode == "repeat":
                        self.assertEqual(report["rejected_decision"]["factor"], "operation")
                cases[f"{bundle}_{mode}"] = report
        artifact = Path(__file__).resolve().parents[1] / ".build/generic-workflow-e2e.json"
        artifact.write_text(json.dumps({"evidence": "offline", "scenarios": cases}, indent=2) + "\n")

    def test_app_preparation_waits_for_the_notes_window(self):
        """Exercise delayed/failed AX window reads before creation through the production CLI."""

        from app.verification import verify_action
        from types import SimpleNamespace
        cases = {}
        for running in (False, True):
            for mode in ("delayed", "unavailable", "app_only"):
                desktop, ax = setup("com.apple.Notes")
                workspace = desktop.workspace
                workspace.bundle = "com.apple.Terminal"
                workspace.URLForApplicationWithBundleIdentifier_ = lambda identifier: identifier if identifier in {"com.apple.Notes", "com.apple.Terminal"} else None
                if running:
                    notes = SimpleNamespace(localizedName=lambda: "Notes", bundleIdentifier=lambda: "com.apple.Notes", processIdentifier=lambda: 456)
                    workspace.runningApplications = lambda: [workspace, notes]
                activated = [False]
                window_reads = [0]
                native_read = ax.AXUIElementCopyAttributeValue
                def read(node, attribute, unused):
                    if activated[0] and node is ax.application and attribute == "AXFocusedWindow":
                        window_reads[0] += 1
                        if mode != "delayed" or window_reads[0] <= 2:
                            return -25204, None
                    return native_read(node, attribute, unused)
                ax.AXUIElementCopyAttributeValue = read
                def activate(url, configuration, callback):
                    workspace.bundle = url
                    workspace.pid = 456 if running else 123
                    workspace.activation_count += 1
                    activated[0] = True
                    callback(workspace, None)
                workspace.openApplicationAtURL_configuration_completionHandler_ = activate
                operation = "FOCUS_APP" if running else "OPEN_APP"
                plan = [(operation if mode == "app_only" else operation + "_FOR_WORKFLOW", "com.apple.Notes", "none" if mode == "app_only" else "span_0"),
                        ("CREATE_NOTE", "com.apple.Notes", "span_0"), ("TYPE_TEXT", "editor", "span_0")]
                class ObservedJev(Jev):
                    def system_one(self, state, questions, *, model=None):
                        if len(self.calls) == 1 and not state["desktop_state"]["focused_window_available"]:
                            self.plan[1] = ("CREATE_NOTE", "none", "span_0")
                        return super().system_one(state, questions, model=model)
                client = ObservedJev(plan=plan)
                goal = "open Notes" if mode == "app_only" else 'create a new note in Notes and type "hello world"'
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "run.json"
                    with patch("app.runtime.verify_action", side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0.3 if mode == "delayed" else 0.01, **kwargs)):
                        status = main([goal, "--report", str(path)], desktop=desktop, client=client, output=io.StringIO())
                    report = json.loads(path.read_text())
                self.assertEqual(workspace.activation_count, 1)
                if mode == "delayed":
                    self.assertEqual(status, 0, report)
                    self.assertEqual(report["outcome"], "completed")
                    self.assertEqual(report["completion_scope"], "verified_workflow")
                    self.assertEqual(len(client.calls), 3)
                    self.assertTrue(client.calls[1]["desktop_state"]["focused_window_available"])
                    self.assertEqual(ax.editor.attrs["AXValue"], "hello world")
                    self.assertEqual(len(ax.effects), 2)
                    self.assertGreaterEqual(window_reads[0], 3)
                elif mode == "unavailable":
                    self.assertEqual(status, 1, report)
                    self.assertEqual(report["outcome"], "unverified")
                    self.assertIn("focused Accessibility window", report["reason"])
                    self.assertEqual(len(client.calls), 1)
                    self.assertFalse(report["steps"][0]["verified"])
                    self.assertEqual(ax.effects, [])
                    self.assertEqual(ax.editor.attrs["AXValue"], "existing note")
                else:
                    self.assertEqual(status, 0, report)
                    self.assertEqual(report["outcome"], "completed")
                    self.assertEqual(len(client.calls), 1)
                    self.assertEqual(window_reads[0], 1)
                    self.assertEqual(ax.effects, [])
                cases[f"{'focus' if running else 'open'}_{mode}"] = {"window_reads": window_reads[0], "provider_calls": len(client.calls), "activation_count": workspace.activation_count, "report": report}
        artifact = Path(__file__).resolve().parents[1] / ".build/workflow-readiness-e2e.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps({"evidence": "offline native/provider boundaries", "scenarios": cases}, indent=2) + "\n")

    def test_reported_workflow_payload_routes_from_text_and_voice(self):
        """Replay the rejected live tuple through activation, note creation and exact insertion."""

        from test_speech_e2e import FakeMicrophone, FakeSpeechModel, FakeStreamingSession
        cases = {}
        artifact = Path(__file__).resolve().parents[1] / ".build/workflow-routing-regression.json"
        artifact.parent.mkdir(exist_ok=True)
        for voice in (False, True):
            for running in (False, True):
                for creation_payload in ("none", "span_0"):
                    goal = 'Open a new note and type Hello World.' if voice else 'create a new note in Notes and type "hello world"'
                    desktop, ax = setup("com.apple.Notes")
                    workspace = desktop.workspace
                    workspace.bundle = "com.apple.Terminal"
                    workspace.URLForApplicationWithBundleIdentifier_ = lambda identifier: identifier if identifier in {"com.apple.Notes", "com.apple.Terminal"} else None
                    if running:
                        from types import SimpleNamespace
                        notes = SimpleNamespace(localizedName=lambda: "Notes", bundleIdentifier=lambda: "com.apple.Notes", processIdentifier=lambda: 456)
                        workspace.runningApplications = lambda: [workspace, notes]
                    def activate(url, configuration, callback):
                        workspace.bundle = url
                        workspace.activation_count += 1
                        workspace.pid = 456 if running else 123
                        callback(workspace, None)
                    workspace.openApplicationAtURL_configuration_completionHandler_ = activate
                    client = Jev(plan=[("FOCUS_APP_FOR_WORKFLOW" if running else "OPEN_APP_FOR_WORKFLOW", "com.apple.Notes", "span_0"),
                                       ("CREATE_NOTE", "com.apple.Notes", creation_payload), ("TYPE_TEXT", "editor", "span_0")])
                    class Session(FakeStreamingSession):
                        def step(self, **kwargs):
                            return [goal] if super().step(**kwargs) else []
                    model = FakeSpeechModel()
                    model.create_streaming_session = lambda **kwargs: Session()
                    with tempfile.TemporaryDirectory() as directory:
                        report_path = Path(directory) / "report.json"
                        argv = ["--listen", "--silence-ms", "200"] if voice else [goal]
                        status = main([*argv, "--yes", "--report", str(report_path)], desktop=desktop, client=client, output=io.StringIO(),
                                      speech_model=model, audio_source_factory=FakeMicrophone)
                        session_report = json.loads(report_path.read_text())
                    report = session_report["runs"][0] if voice else session_report
                    self.assertEqual(status, 0, report)
                    self.assertEqual(report["outcome"], "completed", report)
                    self.assertEqual(report["completion_scope"], "verified_workflow")
                    self.assertEqual(ax.editor.attrs["AXValue"], "Hello World." if voice else "hello world")
                    self.assertEqual(workspace.activation_count, 1)
                    self.assertEqual(len(client.calls), 3)
                    self.assertEqual(client.calls[1]["desktop_state"]["pending_payload_id"], "span_0")
                    self.assertEqual(client.calls[2]["desktop_state"]["pending_payload_id"], "span_0")
                    for step in report["steps"][:2]:
                        self.assertNotIn("text", step["decision"]["selected_candidate"]["parameters"])
                    cases[f"{'voice' if voice else 'text'}_{'focus' if running else 'open'}_create_{creation_payload}"] = session_report
        artifact.write_text(json.dumps({"evidence": "offline", "scenarios": cases}, indent=2) + "\n")

    def test_permission_and_prepared_payload_boundaries(self):
        """Missing permission stops before Jev; preparation never authorizes different text."""

        cases = {}
        for goal in ('Open a new note and type Hello World.', 'create a new note in Notes and type "hello world"', 'type "hello"'):
            desktop, ax = setup("com.apple.Notes")
            ax.trusted = False
            client = Jev()
            report = run_goal(goal, desktop, client=client)
            self.assertEqual(report["outcome"], "failed")
            self.assertIn("Accessibility", report["reason"])
            self.assertIn("--request-accessibility", report["reason"])
            self.assertIn("python_executable", report)
            self.assertEqual(client.calls, [])
            self.assertEqual(ax.effects, [])
            cases[goal] = report
        desktop, ax = setup("com.apple.Notes")
        client = Jev(plan=[("CREATE_NOTE", "com.apple.Notes", "span_0"), ("TYPE_TEXT", "editor", "span_1")])
        report = run_goal('create a new note and type "hello" rather than "goodbye"', desktop, client=client)
        self.assertEqual(report["outcome"], "failed")
        self.assertIn("changed the prepared", report["reason"])
        self.assertEqual(ax.editor.attrs["AXValue"], "")
        self.assertEqual(len(ax.effects), 1)
        cases["prepared_payload_cannot_change"] = report
        # Ordinary app activation still works without Accessibility.
        desktop, ax = setup("com.apple.Notes")
        ax.trusted = False
        workspace = desktop.workspace
        workspace.bundle = "com.apple.Terminal"
        workspace.URLForApplicationWithBundleIdentifier_ = lambda identifier: identifier if identifier in {"com.apple.Notes", "com.apple.Terminal"} else None
        def activate(url, configuration, callback):
            workspace.bundle = url
            workspace.activation_count += 1
            callback(workspace, None)
        workspace.openApplicationAtURL_configuration_completionHandler_ = activate
        report = run_goal("open Notes", desktop, client=Jev(plan=[("OPEN_APP", "com.apple.Notes", "none")]))
        self.assertEqual(report["outcome"], "completed")
        self.assertEqual(workspace.activation_count, 1)
        cases["app_only_without_accessibility"] = report
        artifact = Path(__file__).resolve().parents[1] / ".build/workflow-routing-boundaries.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps({"evidence": "offline", "scenarios": cases}, indent=2) + "\n")

    def test_notes_and_finder_workflows(self):
        cases = {}
        desktop, ax = setup("com.apple.Notes")
        client = Jev(plan=[("CREATE_NOTE", "com.apple.Notes", "none"), ("TYPE_TEXT", "editor", "span_0")])
        report = run_goal('create a new note and type "hello 🦊"', desktop, client=client)
        self.assertEqual(report["outcome"], "completed", report.get("reason"))
        self.assertEqual(ax.editor.attrs["AXValue"], "hello 🦊")
        self.assertEqual(len(client.calls), 2)
        cases["notes_create_type"] = report

        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "resume.txt"
            file.write_text("disposable fixture")
            desktop, ax = setup("com.apple.finder", file.as_uri())
            # The provider chooses the real, locally grounded item ID each time.
            class FinderJev(Jev):
                def system_one(self, state, questions, *, model=None):
                    if len(self.calls) in {1, 2}:
                        operation = "SELECT_ITEM" if len(self.calls) == 1 else "OPEN_ITEM"
                        item = next(key for key in questions[f"target_{operation}"].criteria
                                    if state["semantic_options"]["targets"][key].get("scope") == "finder_item")
                        self.plan[len(self.calls)] = ("SELECT_ITEM" if len(self.calls) == 1 else "OPEN_ITEM", item, "none")
                    return super().system_one(state, questions, model=model)
            client = FinderJev(plan=[("SEARCH", "com.apple.finder", "span_0"), ("STOP", "none", "none"), ("STOP", "none", "none")])
            report = run_goal('search Finder for "resume" and open it', desktop, client=client)
            self.assertEqual(report["outcome"], "completed", report.get("reason"))
            self.assertEqual(ax.editor.attrs["AXValue"], "resume")
            self.assertTrue(ax.item.attrs["AXSelected"])
            self.assertEqual([effect[0] for effect in ax.effects], ["AXValue", "AXSelected", "press"])
            cases["finder_search_select_open"] = report

            desktop, ax = setup("com.apple.finder", file.as_uri())
            original = desktop.snapshot()
            menu = desktop.semantic_menu("open resume", original)
            item = next(key for key, facts in menu.targets.items() if facts.get("scope") == "finder_item")
            # Opening an unselected item cannot bypass the selection operation.
            from app.semantics import SemanticError
            with self.assertRaises(SemanticError):
                menu.resolve("OPEN_ITEM", item, "none")
            ax.item.attrs["AXSelected"] = True
            ax.collection.attrs["AXSelectedRows"] = [ax.item]
            old = desktop.snapshot()
            _, action = desktop.semantic_menu("open resume", old).resolve("OPEN_ITEM", item, "none")
            file.write_text("changed since decision")
            with self.assertRaises(RuntimeError):
                desktop.validate_action(action, old, desktop.snapshot())
            self.assertEqual(ax.effects, [])

            # A selected row is insufficient when Finder also has another selection.
            ax.collection.attrs["AXSelectedRows"] = [ax.item, Node(AXRole="AXRow", AXSelected=True)]
            current = desktop.snapshot()
            with self.assertRaises(SemanticError):
                desktop.semantic_menu("open resume", current).resolve("OPEN_ITEM", item, "none")
            cases["multiple_selection_blocked"] = {"effects": len(ax.effects)}

            desktop, ax = setup("com.apple.finder", file.as_uri())
            duplicate = Node(**{**ax.item.attrs, "AXURL": (Path(directory) / "other.txt").as_uri()})
            Path(directory, "other.txt").write_text("other")
            duplicate.writable.add("AXSelected")
            ax.window.attrs["AXChildren"].append(duplicate)
            menu = desktop.semantic_menu("open resume.txt", desktop.snapshot())
            self.assertFalse(menu.operation_criteria()["SELECT_ITEM"]["available_targets"])
            cases["duplicates_blocked"] = {"effects": len(ax.effects)}

        desktop, ax = setup("com.apple.Notes")
        ax.control.attrs["AXEnabled"] = False
        report = run_goal("create a new note", desktop, client=Jev(plan=[("CREATE_NOTE", "com.apple.Notes", "none")]))
        self.assertNotEqual(report["outcome"], "completed")
        self.assertEqual(ax.effects, [])
        cases["disabled_control"] = report

        desktop, ax = setup("com.apple.Notes")
        report = run_goal('create a new note and type "hello"', desktop, client=Jev(plan=[("CREATE_NOTE", "com.apple.Notes", "none"), ("STOP", "none", "none")]))
        self.assertEqual(report["outcome"], "incomplete")
        self.assertEqual(len(ax.effects), 1)
        cases["stop_before_typing"] = report
        desktop, ax = setup("com.apple.Notes")
        report = run_goal('create a new note and type "hello"', desktop, client=Jev())
        self.assertEqual(report["outcome"], "failed")
        self.assertEqual(report["rejected_decision"]["factor"], "operation")
        self.assertEqual(ax.effects, [])
        cases["typing_cannot_skip_creation"] = report
        artifact = Path(__file__).resolve().parents[1] / ".build/workflow-e2e.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps({"evidence": "offline", "scenarios": cases}, indent=2) + "\n")

    def test_workflow_failure_boundaries(self):
        """Approval, cancellation, stale controls, dry-run and missing proof send no follow-up."""

        cases = {}
        for name in ("low_confidence", "approval_drift", "cancel", "dry_run", "unverified", "repeated", "empty_note", "new_selection"):
            desktop, ax = setup("com.apple.Notes")
            stopping = Event()
            client = Jev(plan=[("CREATE_NOTE", "com.apple.Notes", "none")], confidence=0.4 if name in {"low_confidence", "approval_drift"} else 1)
            approve = lambda _: False
            if name == "approval_drift":
                def approve(_):
                    ax.control.attrs["AXEnabled"] = False
                    return True
            if name == "cancel":
                client.after_call = stopping.set
            if name == "unverified":
                ax.ignore_effect = True
            if name in {"empty_note", "new_selection"}:
                ax.editor.attrs.update(AXValue="", AXSelectedTextRange=(0, 0))
            if name == "new_selection":
                selected = Node(AXRole="AXRow", AXSelected=True)
                ax.window.attrs["AXChildren"].append(selected)
                original_press = ax.AXUIElementPerformAction
                def press(node, action):
                    selected.attrs["AXSelected"] = False
                    ax.window.attrs["AXChildren"].append(Node(AXRole="AXRow", AXSelected=True))
                    return original_press(node, action)
                ax.AXUIElementPerformAction = press
            goal = 'create a note and type "hello"' if name == "repeated" else 'create a note'
            from app.verification import verify_action
            with patch("app.runtime.verify_action", side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0.01, **kwargs)):
                report = run_goal(goal, desktop, client=client, approve=approve, cancel_event=stopping, dry_run=name == "dry_run")
            expected = {"low_confidence": "rejected", "approval_drift": "failed", "cancel": "cancelled", "dry_run": "dry_run", "unverified": "unverified", "repeated": "failed", "empty_note": "unverified", "new_selection": "completed"}[name]
            self.assertEqual(report["outcome"], expected, report)
            self.assertEqual(len(ax.effects), 1 if name in {"unverified", "repeated", "empty_note", "new_selection"} else 0)
            cases[name] = report
        artifact = Path(__file__).resolve().parents[1] / ".build/workflow-failures-e2e.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps({"evidence": "offline", "scenarios": cases}, indent=2) + "\n")
