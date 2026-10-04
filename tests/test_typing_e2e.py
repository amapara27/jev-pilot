"""Exercise the full typing pipeline with fake macOS APIs and a scripted Jev boundary."""

from __future__ import annotations

import io
from hashlib import sha256
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import ApplicationServices as AX

from app.cli import main
from app.desktop import MacDesktop
from app.runtime import run_goal
from app.text_input import NativeText


BUNDLE = "com.apple.TextEdit"


class Node:
    """Represent a native AX identity separately from its mutable attributes."""

    def __init__(self, **attrs):
        self.attrs = attrs
        self.writable = set()


class NativeAX:
    """Model only native AX observation/dispatch; production code owns all safeguards."""

    kAXValueCFRangeType = AX.kAXValueCFRangeType

    def __init__(self, *, value="before ", selection=None, selected_writable=True, focused=True, bundle=BUNDLE):
        self.bundle = bundle
        self.trusted = True
        self.reads, self.effects = [], []
        self.setter_error = self.ignore_insertion = False
        self.window = Node(AXRole="AXWindow", AXEnabled=True)
        self.editor = Node(AXRole="AXTextArea", AXEnabled=True, AXValue=value,
                           AXSelectedTextRange=selection or (len(value.encode("utf-16-le")) // 2, 0),
                           AXDescription="Jev disposable document", AXParent=self.window, AXEditable=True)
        self.editor.writable = {"AXFocused", "AXValue"}
        if selected_writable:
            self.editor.writable.add("AXSelectedText")
        self.window.attrs["AXChildren"] = [self.editor]
        self.application = Node(AXRole="AXApplication", AXFocusedWindow=self.window,
                                AXFocusedUIElement=self.editor if focused else self.window)

    def AXIsProcessTrusted(self):
        return self.trusted

    def AXUIElementCreateApplication(self, pid):
        return self.application

    def AXUIElementSetMessagingTimeout(self, application, timeout):
        return 0

    def AXUIElementCopyAttributeValue(self, node, attribute, unused):
        self.reads.append((node, attribute))
        return (0, node.attrs[attribute]) if attribute in node.attrs else (-25205, None)

    def AXUIElementIsAttributeSettable(self, node, attribute, unused):
        return 0, attribute in node.writable

    def AXValueGetValue(self, value, kind, unused):
        return True, value

    def apply_text(self, text):
        """Simulate native replacement with byte offsets, independently of the verifier."""

        editor = self.application.attrs["AXFocusedUIElement"]
        raw = editor.attrs["AXValue"].encode("utf-16-le")
        start, length = editor.attrs["AXSelectedTextRange"]
        inserted = text.encode("utf-16-le")
        editor.attrs["AXValue"] = (raw[:start * 2] + inserted + raw[(start + length) * 2:]).decode("utf-16-le")
        editor.attrs["AXSelectedTextRange"] = (start + len(inserted) // 2, 0)

    def AXUIElementSetAttributeValue(self, node, attribute, value):
        self.effects.append((attribute, value))
        if attribute == "AXFocused":
            self.application.attrs["AXFocusedUIElement"] = node
        elif attribute == "AXSelectedText" and not self.ignore_insertion:
            self.apply_text(value)
        return -25204 if self.setter_error else 0


class NativeQuartz:
    """Record paired Unicode events and independently simulate their native insertion."""

    def __init__(self, ax):
        self.ax, self.events = ax, []
        self.allowed = True
        self.after_down = None

    def CGPreflightPostEventAccess(self):
        return self.allowed

    def CGEventCreateKeyboardEvent(self, source, code, down):
        return {"code": code, "down": down}

    def CGEventSetFlags(self, event, flags):
        event["flags"] = flags

    def CGEventKeyboardSetUnicodeString(self, event, length, units):
        event["units"] = tuple(units)
        assert length == len(units)

    def CGEventPostToPid(self, pid, event):
        self.events.append(event)
        if event["down"]:
            text = b"".join(unit.to_bytes(2, "little") for unit in event["units"]).decode("utf-16-le")
            self.ax.apply_text(text)
            if self.after_down:
                self.after_down()


class Workspace:
    """Expose native app identities while allowing observable activation preparation."""

    def __init__(self, ax):
        self.ax = ax
        self.pid = 123
        self.bundle = ax.bundle
        self.activation_count = 0

    def frontmostApplication(self):
        return self

    def runningApplications(self):
        return [self]

    def localizedName(self):
        return {BUNDLE: "TextEdit", "com.apple.Terminal": "Terminal", "com.microsoft.VSCode": "Code", "com.apple.Notes": "Notes"}.get(self.bundle, "Safari")

    def bundleIdentifier(self):
        return self.bundle

    def processIdentifier(self):
        return self.pid

    def URLForApplicationWithBundleIdentifier_(self, bundle):
        return object() if bundle in {BUNDLE, "com.apple.Safari"} else None

    def openApplicationAtURL_configuration_completionHandler_(self, url, configuration, callback):
        self.activation_count += 1
        self.pid = 123
        self.bundle = BUNDLE
        callback(self, None)


class Jev:
    """Return exact menu IDs selected by a small explicit test plan."""

    def __init__(self, *, plan=None, after_call=None, confidence=0.99, mutate_answers=None):
        self.plan = plan or [("TYPE_TEXT", "editor", "span_0")]
        self.after_call, self.confidence = after_call, confidence
        self.calls = []
        self.mutate_answers = mutate_answers

    def system_one(self, state, questions, *, model=None):
        index = len(self.calls)
        self.calls.append(state)
        operation, target, payload = self.plan[min(index, len(self.plan) - 1)]
        if target == "editor":
            target = next((key for key, facts in questions["target"].criteria.items() if isinstance(facts, dict) and facts.get("scope") == "field"), "missing_editor")
        selections = {"operation": operation, "target": target, "payload": payload}
        answers = {name: SimpleNamespace(choice=selections[name], probabilities={key: float(key == selections[name]) for key in question.criteria}, confidence=self.confidence) for name, question in questions.items()}
        if self.mutate_answers:
            self.mutate_answers(index, answers)
        if self.after_call:
            self.after_call()
        return SimpleNamespace(choices=answers, model="offline", request_id="typing-test", usage=None)


def setup(**kwargs):
    """Inject native boundaries into the real desktop, scanner, executor, and verifier."""

    ax = NativeAX(**kwargs)
    quartz = NativeQuartz(ax)
    workspace = Workspace(ax)
    desktop = MacDesktop(workspace=workspace, text=NativeText(ax=ax, quartz=quartz, equal=lambda a, b: a is b))
    return desktop, ax, quartz, workspace


class TypingPipelineTests(unittest.TestCase):
    """Leave a JSON artifact proving exact outcomes and non-retry failure behavior."""

    def test_read_only_editor_probe(self):
        """Diagnose native exclusions without reading text, activating, or typing."""

        from scripts.inspect_editors import inspect_editors

        cases = {}
        for name in ("ready", "protected_attribute_error", "protected", "enabled_missing", "untrusted"):
            desktop, ax, quartz, workspace = setup()
            original_read = ax.AXUIElementCopyAttributeValue
            if name == "protected_attribute_error":
                ax.AXUIElementCopyAttributeValue = lambda node, attribute, unused: (
                    (-25204, None) if node is ax.editor and attribute == "AXProtectedContent"
                    else original_read(node, attribute, unused))
            elif name == "protected":
                ax.editor.attrs["AXProtectedContent"] = True
            elif name == "enabled_missing":
                ax.editor.attrs.pop("AXEnabled")
            elif name == "untrusted":
                ax.trusted = False
            report = inspect_editors(BUNDLE, desktop=desktop)
            cases[name] = report
            self.assertEqual(ax.effects, [])
            self.assertEqual(quartz.events, [])
            self.assertEqual(workspace.activation_count, 0)
            self.assertFalse(any(attribute in {"AXValue", "AXSelectedText", "AXSelectedTextRange"} for _, attribute in ax.reads))
            self.assertNotIn("before ", json.dumps(report))
            self.assertEqual(len(report.get("fields", [])), int(name == "ready"))
            if name == "protected_attribute_error":
                self.assertTrue(any("AXProtectedContent:error_-25204" in key for key in report["editor_readiness"]["ancestry_rejections"]))
            elif name == "protected":
                self.assertTrue(any("protected_content" in key for key in report["editor_readiness"]["ancestry_rejections"]))
            elif name == "enabled_missing":
                self.assertEqual(report["editor_readiness"]["excluded_text_fields"], {"enabled_error_-25205": 1})
            elif name == "untrusted":
                self.assertEqual(ax.reads, [])
        artifact = Path(".build/editor-probe-e2e.json")
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps(cases, indent=2) + "\n")

    def test_typing_through_native_boundaries(self):
        cases = {}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"

            def run(name, setup_result, *, goal='type "hello 🦊"', client=None, options=()):
                desktop, ax, quartz, workspace = setup_result
                client = client or Jev()
                output = io.StringIO()
                main([goal, *options, "--report", str(path)], desktop=desktop, client=client, output=output)
                report = json.loads(path.read_text())
                self.assertEqual(json.loads(output.getvalue()), report)
                value = ax.editor.attrs["AXValue"]
                observed = value if len(value) <= 240 else {"length": len(value), "sha256": sha256(value.encode("utf-8")).hexdigest()}
                cases[name] = {"outcome": report["outcome"], "verified": report["verified"],
                               "provider_calls": len(client.calls), "ax_dispatches": len(ax.effects),
                               "keyboard_events": len(quartz.events), "activation_count": workspace.activation_count,
                               "text_after": observed, "selection_after": ax.editor.attrs.get("AXSelectedTextRange")}
                for state in client.calls:
                    for facts in state["semantic_options"]["targets"].values():
                        self.assertNotIn("value", facts)
                        self.assertNotIn("selection", facts)
                        self.assertNotIn("element", facts)
                self.assertNotIn('"value":', json.dumps(report))
                self.assertNotIn('"selection":', json.dumps(report))
                return report

            for name, value, selection, payload, expected in (
                ("insert", "before ", (7, 0), "hello 🦊", "before hello 🦊"),
                ("unicode_selection", "A🦊éZ", (1, 4), "你好", "A你好Z"),
                ("identical_selection", "hello", (0, 5), "hello", "hello"),
                ("multiline", "before ", (7, 0), "first\n  second\t🦊", "before first\n  second\t🦊"),
            ):
                result = setup(value=value, selection=selection)
                report = run(name, result, goal=f'type "{payload}"')
                self.assertEqual(report["outcome"], "completed")
                self.assertEqual(result[1].editor.attrs["AXValue"], expected)
                self.assertEqual(len(result[1].effects), 1)
                self.assertEqual(result[2].events, [])
                self.assertEqual(report["completion_scope"], "single_insertion")

            keyboard = setup(selected_writable=False)
            payload = "界" * 19 + "🦊é" * 12
            report = run("unicode_events", keyboard, goal=f'type "{payload}"')
            self.assertEqual(report["outcome"], "completed")
            self.assertEqual(keyboard[1].editor.attrs["AXValue"], "before " + payload)
            self.assertTrue(all(event["code"] == 0 and event["flags"] == 0 for event in keyboard[2].events))
            self.assertEqual([event["down"] for event in keyboard[2].events], [True, False] * (len(keyboard[2].events) // 2))

            # Notes exposes a broad note table beside a deeply nested body. A
            # focused row or table must not exhaust the budget before the body.
            # Visible children may be absent; either path must stay bounded.
            for visible in (False, True):
                for focused_role in ("AXRow", "AXTable", "AXWindow", "AXTextArea"):
                    for body_first in (False, True):
                        result = setup(bundle="com.apple.Notes")
                        desktop, ax, _, workspace = result
                        workspace.URLForApplicationWithBundleIdentifier_ = lambda bundle: bundle if bundle == "com.apple.Notes" else None
                        ax.editor.attrs["AXIdentifier"] = "Note Body Text View"
                        ax.editor.attrs.pop("AXDescription")
                        split = Node(AXRole="AXSplitGroup", AXParent=ax.window)
                        table = Node(AXRole="AXTable", AXParent=split)
                        rows = []
                        for _ in range(170):
                            row = Node(AXRole="AXRow", AXParent=table)
                            cell = Node(AXRole="AXCell", AXParent=row)
                            cell.attrs["AXChildren"] = [Node(AXRole="AXStaticText", AXParent=cell) for _ in range(5)]
                            row.attrs["AXChildren"] = [cell]
                            rows.append(row)
                        table.attrs["AXChildren"] = rows
                        if visible:
                            table.attrs["AXVisibleChildren"] = rows[:13]
                        body = Node(AXRole="AXScrollArea", AXParent=split, AXIdentifier="Note Body Scroll View")
                        parent = body
                        for _ in range(12):
                            child = Node(AXRole="AXGroup", AXParent=parent)
                            parent.attrs["AXChildren"] = [child]
                            parent = child
                        parent.attrs["AXChildren"] = [ax.editor]
                        ax.editor.attrs["AXParent"] = parent
                        split.attrs["AXChildren"] = [body, table] if body_first else [table, body]
                        ax.window.attrs["AXChildren"] = [split]
                        focused = {"AXRow": rows[0], "AXTable": table, "AXWindow": ax.window, "AXTextArea": ax.editor}[focused_role]
                        ax.application.attrs["AXFocusedUIElement"] = focused
                        plan = [("TYPE_TEXT", "editor", "span_0")] if focused is ax.editor else [("FOCUS_FIELD", "editor", "span_0"), ("TYPE_TEXT", "editor", "span_0")]
                        client = Jev(plan=plan)
                        report = run(f"notes_wide_tree_{visible}_{focused_role}_{body_first}", result, goal='type "hello 🦊" in Notes', client=client)
                        self.assertEqual(report["outcome"], "completed")
                        self.assertEqual(ax.editor.attrs["AXValue"], "before hello 🦊")
                        self.assertLessEqual(report["editor_readiness"]["visited_nodes"], 256)
                        targets = client.calls[0]["semantic_options"]["targets"]
                        body_targets = [facts for facts in targets.values() if facts.get("scope") == "field"]
                        self.assertEqual(len(body_targets), 1)
                        self.assertEqual(body_targets[0]["label"], "Note Body Text View")

            focus = setup(focused=False)
            report = run("focus_then_type", focus, client=Jev(plan=[("FOCUS_FIELD", "editor", "none"), ("TYPE_TEXT", "editor", "span_0")]))
            self.assertEqual(report["outcome"], "completed")
            self.assertEqual(len(focus[1].effects), 2)

            opening = setup()
            opening[3].bundle = "com.apple.Safari"
            report = run("open_then_type", opening, goal='type "hello 🦊" in TextEdit', client=Jev(plan=[("OPEN_APP_FOR_TEXT", BUNDLE, "none"), ("TYPE_TEXT", "editor", "span_0")]))
            self.assertEqual(report["outcome"], "completed")
            self.assertEqual(opening[3].activation_count, 1)
            self.assertEqual(len(opening[1].effects), 1)

            notes = setup(bundle="com.apple.Notes")
            workspace = notes[3]
            workspace.bundle = "com.microsoft.VSCode"
            workspace.URLForApplicationWithBundleIdentifier_ = lambda bundle: bundle if bundle in {"com.apple.Notes", "com.microsoft.VSCode"} else None

            def activate_notes(url, configuration, callback):
                workspace.activation_count += 1
                workspace.bundle = url
                callback(workspace, None)

            workspace.openApplicationAtURL_configuration_completionHandler_ = activate_notes
            client = Jev(plan=[("OPEN_APP_FOR_TEXT", "com.apple.Notes", "span_0"), ("TYPE_TEXT", "editor", "span_0")])
            report = run("notes_from_code_with_payload", notes, goal='type "hello 🦊" in Notes', client=client)
            self.assertEqual(report["outcome"], "completed")
            self.assertEqual(workspace.activation_count, 1)
            self.assertEqual(notes[1].effects, [("AXSelectedText", "hello 🦊")])
            self.assertEqual(client.calls[1]["desktop_state"]["pending_payload_id"], "span_0")

            notes = setup(bundle="com.apple.Notes")
            workspace = notes[3]
            workspace.bundle = "com.microsoft.VSCode"
            workspace.URLForApplicationWithBundleIdentifier_ = lambda bundle: bundle if bundle in {"com.apple.Notes", "com.microsoft.VSCode"} else None
            running_code = SimpleNamespace(localizedName=lambda: "Code", bundleIdentifier=lambda: "com.microsoft.VSCode", processIdentifier=lambda: 123)
            running_notes = SimpleNamespace(localizedName=lambda: "Notes", bundleIdentifier=lambda: "com.apple.Notes", processIdentifier=lambda: 456)
            workspace.runningApplications = lambda: [running_code, running_notes]

            def focus_notes(url, configuration, callback):
                workspace.activation_count += 1
                workspace.bundle, workspace.pid = url, 456
                callback(workspace, None)

            workspace.openApplicationAtURL_configuration_completionHandler_ = focus_notes
            client = Jev(plan=[("FOCUS_APP_FOR_TEXT", "com.apple.Notes", "span_0"), ("TYPE_TEXT", "editor", "span_0")])
            report = run("running_notes_from_code_with_payload", notes, goal='type "hello 🦊" in Notes', client=client)
            self.assertEqual(report["outcome"], "completed")
            self.assertEqual(workspace.activation_count, 1)
            self.assertEqual(notes[1].effects, [("AXSelectedText", "hello 🦊")])

            focus_payload = setup(focused=False)
            report = run("focus_carries_payload", focus_payload, client=Jev(plan=[("FOCUS_FIELD", "editor", "span_0"), ("TYPE_TEXT", "editor", "span_0")]))
            self.assertEqual(report["outcome"], "completed")
            self.assertEqual(len(focus_payload[1].effects), 2)

            drift_payload = setup(focused=False)
            report = run("changed_prepared_payload", drift_payload, goal='type "hello" rather than "goodbye"', client=Jev(plan=[("FOCUS_FIELD", "editor", "span_0"), ("TYPE_TEXT", "editor", "span_1")]))
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(len(drift_payload[1].effects), 1)

            incompatible = setup()
            report = run("rejected_factor_diagnostics", incompatible, client=Jev(plan=[("FOCUS_APP", BUNDLE, "span_0")]))
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(report["rejected_decision"]["choices"], {"operation": "FOCUS_APP", "target": BUNDLE, "payload": "span_0"})
            self.assertEqual(incompatible[1].effects, [])

            missing_body = setup(bundle="com.apple.Notes")
            missing_body[3].URLForApplicationWithBundleIdentifier_ = lambda bundle: bundle if bundle == "com.apple.Notes" else None
            missing_body[1].editor.writable.clear()
            missing_body[1].editor.attrs["AXEditable"] = False
            report = run("notes_no_editable_body", missing_body, goal='type "hello 🦊" in Notes', client=Jev(plan=[("TYPE_TEXT", "com.apple.Notes", "span_0")]))
            self.assertEqual(report["outcome"], "unsupported")
            self.assertIsNone(report["decision"]["selected_candidate"])
            self.assertIn("No editable field was discovered in Notes", report["reason"])
            self.assertEqual(report["editor_readiness"]["field_count"], 0)
            self.assertEqual(missing_body[1].effects, [])

            # Provider totals can be rounded; only a bounded normalization is safe.
            # Gross totals must retain factor/sum diagnostics and send no text,
            # including when an earlier preparation effect already succeeded.
            for total in (0.99, 1.01, 0.9, 1.02):
                result = setup(focused=False)

                def rounded(index, answers):
                    if index != 1:
                        return
                    answer = answers["target"]
                    answer.probabilities[answer.choice] = min(total, 1.0)
                    if total > 1:
                        answer.probabilities["none"] = total - 1

                client = Jev(plan=[("FOCUS_FIELD", "editor", "span_0"), ("TYPE_TEXT", "editor", "span_0")], mutate_answers=rounded)
                report = run(f"probability_total_{total}", result, client=client)
                if 0.99 <= total <= 1.01:
                    self.assertEqual(report["outcome"], "completed")
                    diagnostics = report["decision"]["probability_validation"]["target"]
                    self.assertAlmostEqual(diagnostics["raw_total"], total)
                    self.assertTrue(diagnostics["normalized"])
                    self.assertAlmostEqual(sum(report["decision"]["probabilities"]["target"].values()), 1)
                    self.assertEqual(report["decision"]["confidence"], 0.99)
                else:
                    self.assertEqual(report["outcome"], "failed")
                    self.assertEqual(report["rejected_decision"]["factor"], "target")
                    self.assertAlmostEqual(report["rejected_decision"]["probability_validation"]["target"]["raw_total"], total)
                    self.assertEqual(result[1].effects, [("AXFocused", True)])
                    self.assertEqual(result[2].events, [])

            for name, mutate in (
                ("permission", lambda a: setattr(a, "trusted", False)),
                ("secure", lambda a: a.editor.attrs.update(AXSubrole="AXSecureTextField")),
                ("secure_parent", lambda a: a.window.attrs.update(AXSubrole="AXSecureTextField")),
                ("disabled", lambda a: a.editor.attrs.update(AXEnabled=False)),
                ("read_only", lambda a: (a.editor.writable.clear(), a.editor.attrs.update(AXEditable=False))),
                ("missing_selection", lambda a: a.editor.attrs.pop("AXSelectedTextRange")),
                ("oversized", lambda a: a.editor.attrs.update(AXValue="x" * 65537)),
                ("invalid_range", lambda a: a.editor.attrs.update(AXSelectedTextRange=(999, 0))),
                ("split_surrogate", lambda a: a.editor.attrs.update(AXValue="🦊", AXSelectedTextRange=(1, 0))),
            ):
                result = setup()
                mutate(result[1])
                report = run(name, result)
                self.assertEqual(report["outcome"], "failed")
                self.assertEqual(result[1].effects, [])
                self.assertEqual(result[2].events, [])
                self.assertFalse(report["effect_sent"])
                readiness = report["editor_readiness"]
                self.assertEqual(readiness["can_type_count"], 0)
                if name == "read_only":
                    self.assertEqual(readiness["excluded_text_fields"]["no_write_support"], 1)
                if name == "missing_selection":
                    self.assertEqual(readiness["unready_text_fields"]["missing_selection"], 1)
                if name.startswith("secure"):
                    self.assertNotIn((result[1].editor, "AXValue"), result[1].reads)

            for name, change in (
                ("text_drift", lambda a, w: a.editor.attrs.update(AXValue="changed")),
                ("caret_drift", lambda a, w: a.editor.attrs.update(AXSelectedTextRange=(0, 0))),
                ("foreground_drift", lambda a, w: setattr(w, "pid", 999)),
                ("window_drift", lambda a, w: a.application.attrs.update(AXFocusedWindow=Node(AXRole="AXWindow"))),
                ("replacement", lambda a, w: a.application.attrs.update(AXFocusedUIElement=Node(**a.editor.attrs))),
            ):
                result = setup()
                report = run(name, result, client=Jev(after_call=lambda: change(result[1], result[3])))
                self.assertEqual(report["outcome"], "failed")
                self.assertEqual(result[1].effects, [])
                self.assertEqual(result[2].events, [])
                self.assertFalse(report["effect_sent"])

            for name, preparation in (
                ("setter_uncertain", lambda a, q: setattr(a, "setter_error", True)),
                ("no_effect", lambda a, q: setattr(a, "ignore_insertion", True)),
                ("event_permission", lambda a, q: (a.editor.writable.discard("AXSelectedText"), setattr(q, "allowed", False))),
                ("partial_events", lambda a, q: (a.editor.writable.discard("AXSelectedText"), setattr(q, "after_down", lambda: a.application.attrs.update(AXFocusedUIElement=a.window)))),
            ):
                result = setup()
                preparation(result[1], result[2])
                report = run(name, result, goal='type "' + "🦊" * 25 + '"')
                self.assertEqual(report["outcome"], "unverified" if name == "no_effect" else "failed")
                self.assertLessEqual(len(result[1].effects), 1)
                self.assertLessEqual(len(result[2].events), 2 if name == "partial_events" else 0)

            terminal = setup(value="$ ", bundle="com.apple.Terminal", selected_writable=False)
            report = run("terminal_type_only", terminal, goal='type "echo hello" in Terminal')
            self.assertEqual(report["outcome"], "completed")
            self.assertEqual(terminal[1].editor.attrs["AXValue"], "$ echo hello")
            for name, result, payload in (
                ("terminal_newline", setup(bundle="com.apple.Terminal"), "echo hi\n"),
                ("keyboard_tab", setup(selected_writable=False), "hello\tworld"),
                ("escape", setup(), "hello\x1b"),
            ):
                report = run(name, result, goal=f'type "{payload}"')
                self.assertEqual(report["outcome"], "failed")
                self.assertEqual(result[1].effects, [])
                self.assertEqual(result[2].events, [])

            result = setup()
            report = run("dry_run", result, options=("--dry-run",))
            self.assertEqual(report["outcome"], "dry_run")
            self.assertEqual(result[1].effects, [])
            result = setup()
            report = run("low_confidence", result, client=Jev(confidence=0.3))
            self.assertEqual(report["outcome"], "rejected")
            self.assertEqual(result[1].effects, [])
            result = setup(focused=False)
            report = run("early_stop", result, client=Jev(plan=[("FOCUS_FIELD", "editor", "none"), ("STOP", "none", "none")]))
            self.assertEqual(report["outcome"], "incomplete")
            self.assertEqual(len(result[1].effects), 1)

            result = setup()
            workspace = result[3]
            workspace.URLForApplicationWithBundleIdentifier_ = lambda bundle: bundle if bundle in {BUNDLE, "com.apple.Safari"} else None

            def activate(url, configuration, callback):
                workspace.activation_count += 1
                workspace.bundle = url
                callback(workspace, None)

            workspace.openApplicationAtURL_configuration_completionHandler_ = activate
            report = run("preparation_limit", result, client=Jev(plan=[
                ("OPEN_APP_FOR_TEXT", "com.apple.Safari", "none"),
                ("OPEN_APP_FOR_TEXT", BUNDLE, "none"),
                ("OPEN_APP_FOR_TEXT", "com.apple.Safari", "none"),
                ("OPEN_APP_FOR_TEXT", BUNDLE, "none"),
            ]))
            self.assertEqual(report["outcome"], "incomplete")
            self.assertEqual(workspace.activation_count, 4)
            self.assertEqual(result[1].effects, [])

        artifact = Path(__file__).resolve().parents[1] / ".build/typing-e2e.json"
        artifact.write_text(json.dumps({"schema_version": 1, "evidence": "offline native boundaries", "scenarios": cases}, indent=2, sort_keys=True) + "\n")

    def test_approval_does_not_transfer_to_a_changed_selection(self):
        """Exercise the real approval boundary with a caret change during the prompt."""

        desktop, ax, _, _ = setup()

        def approve(action):
            ax.editor.attrs["AXSelectedTextRange"] = (0, 0)
            return True

        report = run_goal('type "hello"', desktop, client=Jev(confidence=0.3), approve=approve)
        self.assertEqual(report["outcome"], "failed")
        self.assertEqual(ax.effects, [])


if __name__ == "__main__":
    unittest.main()
