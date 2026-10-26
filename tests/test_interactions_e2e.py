"""Exercise explicit input and context guards through the production CLI pipeline."""

import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import os
from threading import Thread
from time import monotonic, sleep
import tempfile
import numpy as np

from app.cli import main
from app.runtime import run_goal
from test_typing_e2e import setup, Jev, Node, NativeQuartz
from test_speech_e2e import FakeStreamingSession, FakeMicrophone, FakeSpeechModel
from test_cli_e2e import FakeDesktop, FakeJev, SAFARI, TERMINAL


def input_setup(**kwargs):
    """Expose native selection-range support without changing insertion fixtures."""

    desktop, ax, quartz, workspace = setup(**kwargs)
    ax.editor.writable.add('AXSelectedTextRange')
    ax.AXValueCreate = lambda kind, value: value
    setter = ax.AXUIElementSetAttributeValue

    def set_value(node, attribute, value):
        if attribute == 'AXSelectedTextRange':
            node.attrs[attribute] = value
            ax.effects.append((attribute, value))
            return 0
        return setter(node, attribute, value)

    ax.AXUIElementSetAttributeValue = set_value
    return desktop, ax, quartz, workspace


class InteractionQuartz(NativeQuartz):
    """Simulate key/scroll boundaries without calling the real desktop."""

    kCGScrollEventUnitLine = 1
    kCGScrollEventUnitPixel = 0

    def CGEventCreateScrollWheelEvent(self, source, unit, count, vertical, horizontal):
        return {"scroll": (vertical, horizontal)}

    def CGEventSetLocation(self, event, point):
        event["point"] = point

    def CGEventPostToPid(self, pid, event):
        if "units" in event:
            return super().CGEventPostToPid(pid, event)
        self.events.append(event)
        if "scroll" in event:
            self.bar.attrs["AXValue"] = min(1, max(0, self.bar.attrs["AXValue"] - event["scroll"][0] / 100))
        elif event["down"]:
            if event["code"] == 36:
                self.ax.apply_text("\n")


class InteractionJev(Jev):
    """Select grounded action IDs from shared facts instead of knowing native handles."""

    def system_one(self, state, questions, **kwargs):
        operation, command, payload = self.plan[min(len(self.calls), len(self.plan) - 1)]
        if command.startswith("action:"):
            command = next((key for key in questions[f"target_{operation}"].criteria
                            if state["semantic_options"]["targets"][key].get("command") == command[7:]), "missing")
        original = self.plan
        self.plan = [(operation, command, payload)] * (len(self.calls) + 1)
        try:
            return super().system_one(state, questions, **kwargs)
        finally:
            self.plan = original


class InputActionsEndToEnd(unittest.TestCase):
    """Persist verified workflows and prove unavailable or stale effects stay unsent."""

    def test_type_then_enter_and_literal_payload(self):
        records = []
        for goal, text, payload in [('type "hello and press enter"', 'hello and press enter', 'span_0'),
                                    ('type search for messi soccer videos on youtube', 'search for messi soccer videos on youtube', 'span_0'),
                                    ('type hello world and hit enter', 'hello world', 'span_1')]:
            desktop, ax, _, _ = input_setup(value="")
            desktop.text.quartz = InteractionQuartz(ax)
            plan = [("TYPE_TEXT", "editor", payload)]
            if goal.endswith('hit enter'):
                plan.append(("KEYBOARD_ACTION", "action:enter", "none"))
            result = run_goal(goal, desktop, client=InteractionJev(plan=plan))
            self.assertEqual(result["outcome"], "completed", result)
            self.assertEqual(ax.editor.attrs["AXValue"], text + ('\n' if len(plan) == 2 else ''))
            self.assertEqual(sum(step["decision"]["semantic"]["operation"] == "TYPE_TEXT" for step in result["steps"]), 1)
            records.append(result)
        Path('.build/interactions-e2e.json').write_text(json.dumps(records, indent=2))

    def test_select_all_requires_text_and_fresh_focus(self):
        desktop, ax, _, _ = input_setup(value="hello")
        desktop.text.quartz = InteractionQuartz(ax)
        result = run_goal('select all', desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:select_all', 'none')]))
        self.assertEqual(result['outcome'], 'completed', result)
        self.assertEqual(ax.editor.attrs['AXSelectedTextRange'], (0, 5))
        for value, drift in [("", False), ("hello", True)]:
            desktop, ax, _, _ = input_setup(value=value)
            desktop.text.quartz = InteractionQuartz(ax)
            result = run_goal('select all', desktop, client=InteractionJev(
                plan=[('KEYBOARD_ACTION', 'action:select_all', 'none')],
                after_call=(lambda: ax.editor.attrs.update(AXValue='changed')) if drift else None))
            self.assertFalse(result['effect_sent'], result)

    def test_scroll_requires_observed_range_and_verifies_movement(self):
        desktop, ax, _, _ = input_setup()
        ax.kAXValueCGPointType = 1
        ax.kAXValueCGSizeType = 2
        quartz = InteractionQuartz(ax)
        desktop.text.quartz = quartz
        # Scrolling sets the scrollbar position from measured geometry; wheel events are never posted.
        quartz.bar = Node(AXRole='AXScrollBar', AXValue=0.2, AXMinValue=0, AXMaxValue=1)
        quartz.bar.writable.add('AXValue')
        ax.AXUIElementSetAttributeValue = lambda node, attribute, value: (node.attrs.update({attribute: value}) or 0)
        content = Node(AXRole='AXGroup', AXSize=(400, 1500))
        area = Node(AXRole='AXScrollArea', AXParent=ax.window, AXVerticalScrollBar=quartz.bar, AXContents=[content],
                    AXPosition=(10, 20), AXSize=(400, 300), AXChildren=[ax.editor])
        ax.window.attrs['AXChildren'] = [area]
        ax.editor.attrs['AXParent'] = area
        result = run_goal('scroll down a lot', desktop, client=InteractionJev(plan=[('SCROLL', 'action:scroll_down_large', 'none')]))
        self.assertEqual(result['outcome'], 'completed', result)
        # 600 px over a 1200 px scrollable extent moves half the 0–1 range.
        self.assertAlmostEqual(quartz.bar.attrs['AXValue'], 0.7)
        self.assertEqual(quartz.events, [])
        quartz.bar.attrs['AXValue'] = 1
        result = run_goal('scroll down', desktop, client=InteractionJev(plan=[('SCROLL', 'action:scroll_down', 'none')]))
        self.assertFalse(result['effect_sent'], result)
        quartz.bar.attrs['AXValue'] = 0.5
        result = run_goal('go to the top', desktop, client=InteractionJev(plan=[('SCROLL', 'action:scroll_top', 'none')]))
        self.assertEqual(result['outcome'], 'completed', result)
        self.assertEqual(quartz.bar.attrs['AXValue'], 0)
        self.assertEqual(quartz.events, [])
        Path('.build/scroll-e2e.json').write_text(json.dumps(result, indent=2))

    def test_input_failure_boundaries_and_exact_enter_context(self):
        records = []
        for mode in ('tail_as_text', 'changed_after_insertion', 'secure', 'permission', 'no_scroll'):
            desktop, ax, _, _ = input_setup(value='')
            quartz = InteractionQuartz(ax)
            desktop.text.quartz = quartz
            if mode == 'secure':
                ax.editor.attrs['AXSubrole'] = 'AXSecureTextField'
            if mode == 'permission':
                quartz.allowed = False
            plan = [('TYPE_TEXT', 'editor', 'span_0' if mode == 'tail_as_text' else 'span_1'),
                    ('KEYBOARD_ACTION', 'action:enter', 'none')]
            if mode == 'changed_after_insertion':
                # The next observation must detect drift after exact insertion verification.
                snapshot = desktop.snapshot
                changed = [False]

                def observe():
                    state = snapshot()
                    if ax.editor.attrs['AXValue'] == 'hello' and not changed[0]:
                        changed[0] = True
                        ax.editor.attrs['AXValue'] = 'user edit'
                    return state

                desktop.snapshot = observe
            goal = 'scroll down' if mode == 'no_scroll' else 'type hello and hit enter'
            if mode == 'no_scroll':
                plan = [('SCROLL', 'action:scroll_down', 'none')]
            result = run_goal(goal, desktop, client=InteractionJev(plan=plan), approve=lambda _: True)
            self.assertNotEqual(result['outcome'], 'completed', result)
            self.assertEqual(quartz.events, [], result)
            records.append(result)
        Path('.build/interaction-failures-e2e.json').write_text(json.dumps(records, indent=2))

    def test_terminal_enter_and_auto_approval_boundaries(self):
        desktop, ax, _, _ = input_setup(bundle='com.apple.Terminal')
        result = run_goal('press enter', desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:enter', 'none')]), approve=lambda _: True)
        self.assertFalse(result['effect_sent'], result)
        from test_cli_e2e import FakeDesktop, FakeJev
        path = Path('.build/auto-approve-e2e.json')
        status = main(['switch to Terminal', '--auto-approve-basic', '--report', str(path)],
                      desktop=FakeDesktop(), client=FakeJev(confidence=0.3), output=io.StringIO())
        self.assertEqual(status, 0)

    def test_yes_skips_routine_workflow_prompts_in_text_and_voice(self):
        """Automatic routine controls still stop for unknown or destructive controls."""

        from test_workflow_e2e import panel_button, setup as workflow_setup
        records = {}
        for voice in (False, True):
            for label, expected in [('New Document', 'completed'), ('Perform Unknown Task', 'rejected'),
                                    ('Delete Document', 'blocked')]:
                desktop, ax = workflow_setup('com.apple.TextEdit')
                panel_button(ax, label)

                def press(node, action):
                    ax.effects.append(('press', label))
                    old = ax.editor
                    ax.editor = Node(**{**old.attrs, 'AXValue': '', 'AXSelectedTextRange': (0, 0)})
                    ax.editor.writable = set(old.writable)
                    ax.window.attrs['AXChildren'][0] = ax.editor
                    ax.application.attrs['AXFocusedUIElement'] = ax.editor
                    return 0

                ax.AXUIElementPerformAction = press

                class Client(InteractionJev):
                    def system_one(self, state, questions, **kwargs):
                        if not self.calls:
                            control = next(key for key in questions['target_PRESS_CONTROL'].criteria
                                           if state['semantic_options']['targets'][key]['label'] == label)
                            self.plan[0] = ('PRESS_CONTROL', control, 'span_0')
                        return super().system_one(state, questions, **kwargs)

                client = Client(confidence=0.2, plan=[('PRESS_CONTROL', '', 'span_0'), ('TYPE_TEXT', 'editor', 'span_0')])
                goal = 'create a new document in TextEdit and type "hello world"'

                def capture(model, on_final, **kwargs):
                    on_final(goal)
                    return 1

                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'report.json'
                    with patch('app.speech_runtime.run_live_asr', side_effect=capture), \
                            patch('app.cli.sys.stdin.isatty', return_value=True), \
                            patch('app.cli._confirm_action', return_value=False) as prompt:
                        status = main([*(['--listen'] if voice else [goal]), '--yes', '--report', str(path)],
                                      desktop=desktop, client=client, output=io.StringIO(), speech_model=FakeSpeechModel())
                    report = json.loads(path.read_text())
                result = report['runs'][0] if voice else report
                self.assertEqual(result['outcome'], expected, result)
                self.assertEqual(status, int(expected != 'completed'))
                self.assertEqual(prompt.call_count, int(expected == 'rejected'))
                self.assertEqual(ax.editor.attrs['AXValue'], 'hello world' if expected == 'completed' else 'existing note')
                self.assertEqual(len(ax.effects), 2 if expected == 'completed' else 0)
                records[f'{"voice" if voice else "text"}_{label}'] = report
        Path('.build/yes-workflow-approvals-e2e.json').write_text(json.dumps(records, indent=2))

    def test_yes_keeps_risky_interaction_prompts_in_text_and_voice(self):
        """Cut, tab closure outside Safari and generic Enter never inherit routine auto-approval."""

        records = {}
        for voice in (False, True):
            for command, goal, label, bundle in [('cut', 'cut', 'Cut', 'com.apple.TextEdit'),
                                                ('close_tab', 'close this tab', 'Close Tab', 'com.google.Chrome'),
                                                ('enter', 'hit enter', '', 'com.example.Editor')]:
                desktop, ax, _, _ = input_setup(value='hello', selection=(0, 5), bundle=bundle)
                desktop.interactions.pasteboard = SimpleNamespace(changeCount=lambda: 1)
                menu = Node(AXRole='AXMenuItem', AXTitle=label, AXEnabled=True)
                ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu])
                ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
                client = InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + command, 'none')])

                def capture(model, on_final, **kwargs):
                    on_final(goal)
                    return 1

                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'report.json'
                    with patch('app.speech_runtime.run_live_asr', side_effect=capture), \
                            patch('app.cli.sys.stdin.isatty', return_value=True), \
                            patch('app.cli._confirm_action', return_value=False) as prompt:
                        main([*(['--listen'] if voice else [goal]), '--yes', '--report', str(path)],
                             desktop=desktop, client=client, output=io.StringIO(), speech_model=FakeSpeechModel())
                    report = json.loads(path.read_text())
                result = report['runs'][0] if voice else report
                self.assertEqual(result['outcome'], 'rejected', result)
                self.assertEqual(prompt.call_count, 1)
                self.assertFalse(result['effect_sent'])
                self.assertEqual(ax.effects, [])
                records[f'{"voice" if voice else "text"}_{command}'] = report
        Path('.build/yes-risky-approvals-e2e.json').write_text(json.dumps(records, indent=2))

    def test_ordered_menu_commands_and_approval(self):
        records = []
        for approval in (False, True):
            desktop, ax, _, _ = input_setup(value='hello')
            desktop.text.quartz = InteractionQuartz(ax)
            clipboard = SimpleNamespace(count=0)
            desktop.interactions.pasteboard = SimpleNamespace(changeCount=lambda: clipboard.count)
            copy = Node(AXRole='AXMenuItem', AXTitle='Copy', AXEnabled=True)
            cut = Node(AXRole='AXMenuItem', AXTitle='Cut', AXEnabled=True)
            ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[copy, cut])
            ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node in (copy, cut) else [])

            def press(node, action):
                clipboard.count += 1
                if node is cut:
                    ax.apply_text('')
                return 0

            ax.AXUIElementPerformAction = press
            client = InteractionJev(plan=[('KEYBOARD_ACTION', 'action:select_all', 'none'),
                                          ('KEYBOARD_ACTION', 'action:copy', 'none'),
                                          ('KEYBOARD_ACTION', 'action:cut', 'none')])
            result = run_goal('select all and copy and cut', desktop, client=client, approve=lambda _: approval)
            self.assertEqual(result['outcome'], 'completed' if approval else 'rejected', result)
            self.assertEqual(clipboard.count, 2 if approval else 1)
            self.assertEqual(ax.editor.attrs['AXValue'], '' if approval else 'hello')
            records.append(result)
        Path('.build/keyboard-menu-e2e.json').write_text(json.dumps(records, indent=2))

    def test_browser_commands_require_the_current_app_and_enabled_menu(self):
        records = []
        for bundle, enabled in [('com.apple.Safari', True), ('com.apple.Safari', False), ('com.apple.TextEdit', True)]:
            desktop, ax, _, _ = input_setup(bundle=bundle)
            button = Node(AXRole='AXMenuItem', AXTitle='Show Next Tab', AXEnabled=enabled)
            first, second = Node(AXRole='AXRadioButton', AXSelected=True), Node(AXRole='AXRadioButton', AXSelected=False)
            tabs = Node(AXRole='AXTabGroup', AXChildren=[first, second], AXParent=ax.window)
            ax.window.attrs['AXChildren'].append(tabs)
            ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[button])
            ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is button else [])
            presses = []

            def press(node, action):
                presses.append(node)
                first.attrs['AXSelected'], second.attrs['AXSelected'] = False, True
                return 0

            ax.AXUIElementPerformAction = press
            result = run_goal('next tab', desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:next_tab', 'none')]))
            allowed = bundle == 'com.apple.Safari' and enabled
            self.assertEqual(result['outcome'] == 'completed', allowed, result)
            self.assertEqual(len(presses), int(allowed))
            records.append(result)
        Path('.build/browser-keyboard-e2e.json').write_text(json.dumps(records, indent=2))

    def test_continuous_speech_recovers_and_stops_explicitly(self):
        """Keep capture active after a blocked goal and checkpoint every completed goal."""

        transcripts = ['Switch to Terminal', 'delete everything', 'Switch to Safari', 'stop listening']

        class Session(FakeStreamingSession):
            def step(self, **kwargs):
                return [self.transcript if value else value for value in super().step(**kwargs)]

        class Model:
            sessions = 0

            def create_streaming_session(self, **kwargs):
                session = Session()
                session.transcript = transcripts[min(self.sessions, len(transcripts) - 1)]
                self.sessions += 1
                return session

        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / 'voice.json'

            class Source(FakeMicrophone):
                error = None

                def start(self):
                    def feed():
                        for index in range(len(transcripts)):
                            self.callback(np.full((100, 1), 0.5, dtype=np.float32))
                            self.callback(np.zeros((100, 1), dtype=np.float32))
                            self.callback(np.zeros((100, 1), dtype=np.float32))
                            if index == len(transcripts) - 1:
                                break
                            deadline = monotonic() + 8
                            while monotonic() < deadline:
                                if report_path.exists() and len(json.loads(report_path.read_text())['runs']) >= index + 1:
                                    break
                                sleep(0.01)
                            else:
                                self.error = 'Session did not checkpoint or resume.'
                                break
                        self.done = True
                    self.thread = Thread(target=feed, daemon=True)
                    self.thread.start()

                def stop(self):
                    self.done = True

            class Client(FakeJev):
                def system_one(self, state, questions, **kwargs):
                    self.target = TERMINAL.bundle_id if not self.calls else SAFARI.bundle_id
                    return super().system_one(state, questions, **kwargs)

            desktop, client = FakeDesktop(), Client(confidence=0.3)
            status = main(['--listen', '--auto-approve-basic', '--silence-ms', '200', '--report', str(report_path)],
                          desktop=desktop, client=client, output=io.StringIO(), speech_model=Model(), audio_source_factory=Source)
            report = json.loads(report_path.read_text())
            self.assertEqual(status, 1)  # Session records the blocked command even after recovery.
            self.assertEqual([item['outcome'] for item in report['runs']], ['completed', 'blocked', 'completed'], report)
            self.assertEqual(report['transcripts'], transcripts)
            self.assertEqual(desktop.effects, 2)
            Path('.build/continuous-voice-e2e.json').write_text(json.dumps(report, indent=2))

    @unittest.skipUnless(os.environ.get('JEV_LIVE_JEV') == '1', 'Live Jev opt-in; native services are synthetic.')
    def test_live_input_progression(self):
        """Check real Jev decisions without posting native events or reading documents."""

        records = []
        for goal in ['type hello world and hit enter', 'select all']:
            desktop, ax, _, _ = input_setup(value='' if goal.startswith('type') else 'hello')
            desktop.text.quartz = InteractionQuartz(ax)
            result = run_goal(goal, desktop, approve=lambda _: True)
            records.append(result)
            Path('.build/live-interactions-e2e.json').write_text(json.dumps(records, indent=2))
            self.assertEqual(result['outcome'], 'completed', result)


if __name__ == '__main__':
    unittest.main()
