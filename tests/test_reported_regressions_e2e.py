"""Replay reported routing, focus, menu and Finder failures through the real pipeline."""

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tempfile

from app.cli import main
from app.runtime import run_goal
from app.desktop import AppState
from app.text_input import NativeText
from test_typing_e2e import Jev, Node
from test_interactions_e2e import input_setup, InteractionJev, InteractionQuartz
from test_workflow_e2e import setup as workflow_setup
from test_cli_e2e import FakeDesktop, FakeJev


class ReportedRegressions(unittest.TestCase):
    """Leave bounded artifacts and prove that wrong-context decisions send no effect."""

    def test_named_apps_and_notes_workflows_cannot_choose_other_apps(self):
        records = []
        for operation, target, expected in [('OPEN_APP', 'com.apple.Notes', True),
                                             ('OPEN_APP_FOR_WORKFLOW', 'com.apple.TextEdit', False)]:
            desktop = FakeDesktop()
            desktop.workspace.installed.update({'com.apple.Notes', 'com.apple.TextEdit', 'notion.id'})
            desktop.applications = (('Notion', 'notion.id', ('notion',)),)
            desktop.execute = lambda action: setattr(desktop, 'active', AppState('Notes', action.parameters['bundle_identifier'], 123))
            result = run_goal('open notes', desktop, client=FakeJev(operation=operation, target=target))
            self.assertEqual(result['outcome'] == 'completed', expected, result)
            self.assertEqual(result['effect_sent'], expected)
            records.append(result)
        desktop, ax = workflow_setup('com.apple.Notes')
        desktop.workspace.bundle = 'com.apple.TextEdit'
        desktop.workspace.URLForApplicationWithBundleIdentifier_ = lambda bundle: bundle if bundle in {'com.apple.Notes', 'com.apple.TextEdit', 'com.apple.Safari', 'notion.id'} else None
        desktop.workspace.openApplicationAtURL_configuration_completionHandler_ = lambda url, configuration, callback: (setattr(desktop.workspace, 'bundle', url), callback(desktop.workspace, None))
        result = run_goal('create a new note in Notes and type "hello world"', desktop,
                          client=Jev(plan=[('OPEN_APP_FOR_WORKFLOW', 'com.apple.Notes', 'span_0'),
                                           ('CREATE_NOTE', 'com.apple.Notes', 'span_0'), ('TYPE_TEXT', 'editor', 'span_0')]))
        self.assertEqual(result['outcome'], 'completed', result)
        self.assertEqual(ax.editor.attrs['AXValue'], 'hello world')
        records.append(result)
        desktop = FakeDesktop()
        result = run_goal('open Safari', desktop, client=FakeJev(operation='STOP', target='none'))
        self.assertEqual(result['outcome'], 'completed', result)
        self.assertFalse(result['effect_sent'])
        records.append(result)
        Path('.build/reported-routing-e2e.json').write_text(json.dumps(records, indent=2))

    def test_paste_focuses_the_existing_notes_body_and_keeps_clipboard_local(self):
        desktop, ax, _, _ = input_setup(bundle='com.apple.Notes', value='', focused=False)
        clipboard = 'Copied text 🦊\nSecond line'
        desktop.interactions.pasteboard = SimpleNamespace(changeCount=lambda: 17, stringForType_=lambda kind: clipboard)
        paste = Node(AXRole='AXMenuItem', AXTitle='Paste', AXEnabled=True)
        ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[paste])
        ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is paste else [])
        ax.AXUIElementPerformAction = lambda node, action: (ax.apply_text(clipboard) or 0)
        result = run_goal('paste the text in this note', desktop, client=InteractionJev(
            plan=[('FOCUS_FIELD', 'editor', 'none'), ('KEYBOARD_ACTION', 'action:paste', 'none')]), approve=lambda _: True)
        self.assertEqual(result['outcome'], 'completed', result)
        self.assertEqual(ax.editor.attrs['AXValue'], clipboard)
        self.assertNotIn(clipboard, json.dumps(result))
        Path('.build/reported-paste-e2e.json').write_text(json.dumps(result, indent=2))

    def test_browser_menu_actions_ignore_unrelated_address_caret(self):
        records = []
        for command, title in [('next_tab', 'Show Next Tab'), ('zoom_in', 'Zoom In'), ('back', 'Back')]:
            desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
            menu = Node(AXRole='AXMenuItem', AXTitle=title, AXEnabled=True)
            first, second = Node(AXRole='AXRadioButton', AXSelected=True), Node(AXRole='AXRadioButton', AXSelected=False)
            group = Node(AXRole='AXTabGroup', AXChildren=[first, second], AXParent=ax.window)
            indicator = Node(AXRole='AXButton', AXDescription='Page Zoom', AXValue='100%', AXParent=ax.window)
            ax.window.attrs['AXChildren'].extend([group, indicator])
            ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu])
            ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
            ax.window.attrs['AXURL'] = 'https://example.com/two'
            def press(node, action):
                if command == 'next_tab':
                    first.attrs['AXSelected'], second.attrs['AXSelected'] = False, True
                elif command == 'zoom_in':
                    indicator.attrs['AXValue'] = '125%'
                else:
                    ax.window.attrs['AXURL'] = 'https://example.com/one'
                return 0
            ax.AXUIElementPerformAction = press
            def drift():
                ax.editor.attrs['AXSelectedTextRange'] = (0, 0)
                ax.application.attrs['AXFocusedUIElement'] = ax.window
            result = run_goal({'next_tab':'switch to the next tab', 'zoom_in':'zoom in', 'back':'go back'}[command],
                              desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + command, 'none')], after_call=drift))
            self.assertEqual(result['outcome'], 'completed', result)
            records.append(result)
        Path('.build/reported-browser-e2e.json').write_text(json.dumps(records, indent=2))

    def test_webkit_scrollbar_without_min_max_is_grounded(self):
        records = []
        for mode in ('attribute', 'child', 'exhausted', 'range_error'):
            desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
            ax.kAXValueCGPointType, ax.kAXValueCGSizeType = 1, 2
            quartz = InteractionQuartz(ax)
            desktop.text.quartz = quartz
            quartz.bar = Node(AXRole='AXScrollBar', AXValue=1 if mode == 'exhausted' else 0.4, AXOrientation='AXVerticalOrientation')
            area = Node(AXRole='AXScrollArea', AXPosition=(0, 0), AXSize=(600, 400), AXParent=ax.window, AXChildren=[ax.editor, quartz.bar])
            if mode != 'child':
                area.attrs['AXVerticalScrollBar'] = quartz.bar
            ax.window.attrs['AXChildren'] = [area]
            ax.editor.attrs['AXParent'] = area
            if mode == 'range_error':
                read = ax.AXUIElementCopyAttributeValue
                ax.AXUIElementCopyAttributeValue = lambda node, attribute, unused: (-25204, None) if node is quartz.bar and attribute in {'AXMinValue', 'AXMaxValue'} else read(node, attribute, unused)
            result = run_goal('scroll down', desktop, client=InteractionJev(plan=[('SCROLL', 'action:scroll_down', 'none')]))
            self.assertEqual(result['outcome'] == 'completed', mode in {'attribute', 'child'}, result)
            if mode in {'attribute', 'child'}:
                self.assertGreater(quartz.bar.attrs['AXValue'], 0.4)
            else:
                self.assertFalse(result['effect_sent'])
                self.assertFalse(quartz.events)
            records.append(result)
        Path('.build/reported-scroll-e2e.json').write_text(json.dumps(records, indent=2))

    def test_finder_search_finds_hidden_query_and_submits_once(self):
        records = []
        for mode in ('normal', 'disabled', 'drift'):
            desktop, ax = workflow_setup('com.apple.finder')
            ax.editor.attrs.pop('AXEnabled')
            ax.application.attrs['AXFocusedUIElement'] = ax.window
            ax.window.attrs['AXChildren'] = [Node(AXRole='AXGroup', AXChildren=[Node(AXRole='AXButton') for _ in range(300)])]
            original = ax.AXUIElementPerformAction
            def press(node, action):
                result = original(node, action)
                if node is ax.control:
                    ax.window.attrs['AXChildren'].insert(0, ax.editor)
                    if mode == 'disabled':
                        ax.editor.attrs['AXEnabled'] = False
                return result
            ax.AXUIElementPerformAction = press
            quartz = InteractionQuartz(ax)
            desktop.text.quartz = quartz
            submitted = []
            quartz.CGEventPostToPid = lambda pid, event: submitted.append(event.copy())
            def drift():
                if mode == 'drift' and ax.editor in ax.window.attrs['AXChildren']:
                    ax.editor.attrs['AXValue'] = 'changed query'
            result = run_goal('search Finder for invoices', desktop, client=Jev(
                plan=[('PREPARE_SEARCH', 'com.apple.finder', 'span_0'), ('SEARCH', 'com.apple.finder', 'span_0')], after_call=drift))
            self.assertEqual(result['outcome'] == 'completed', mode == 'normal', result)
            if mode == 'normal':
                self.assertEqual(ax.editor.attrs['AXValue'], 'invoices')
                self.assertEqual([(event['code'], event['down']) for event in submitted], [(36, True), (36, False)])
            else:
                self.assertFalse(submitted)
            records.append(result)
        Path('.build/reported-finder-e2e.json').write_text(json.dumps(records, indent=2))

    def test_approval_restores_original_process_and_revalidates_editor(self):
        records = []
        for drift in (False, True):
            desktop, ax, _, workspace = input_setup(value='hello', selection=(0, 5))
            cut = Node(AXRole='AXMenuItem', AXTitle='Cut', AXEnabled=True)
            ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[cut])
            count = [1]
            desktop.interactions.pasteboard = SimpleNamespace(changeCount=lambda: count[0])
            ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is cut else [])
            def press(node, action):
                ax.apply_text('')
                count[0] += 1
                return 0
            ax.AXUIElementPerformAction = press
            original_front = workspace.frontmostApplication
            def restore(options):
                workspace.pid, workspace.bundle = 123, 'com.apple.TextEdit'
                return True
            application = SimpleNamespace(processIdentifier=lambda: 123, activateWithOptions_=restore,
                                          localizedName=lambda: 'TextEdit', bundleIdentifier=lambda: 'com.apple.TextEdit')
            workspace.frontmostApplication = lambda: application if workspace.pid == 123 else original_front()
            def answer(prompt):
                workspace.pid, workspace.bundle = 999, 'com.apple.Terminal'
                if drift:
                    ax.editor.attrs['AXValue'] = 'different text'
                return 'y'
            with tempfile.TemporaryDirectory() as directory, patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=answer):
                path = Path(directory) / 'report.json'
                status = main(['cut', '--report', str(path)], desktop=desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:cut', 'none')]), output=io.StringIO())
                result = json.loads(path.read_text())
            self.assertEqual(status == 0, not drift, result)
            self.assertEqual(result['effect_sent'], not drift, result)
            self.assertEqual(workspace.pid, 123)
            records.append(result)
        Path('.build/reported-approval-e2e.json').write_text(json.dumps(records, indent=2))

    def test_safari_tab_selection_closure_and_repeated_zoom(self):
        records = []
        for command, goal, title in [('previous_tab', 'go back to the previous tab actually', 'Show Previous Tab'),
                                     ('select_tab', 'click on the YouTube tab', 'YouTube'),
                                     ('close_tab', 'close this tab', 'Close Tab'),
                                     ('zoom_in', 'zoom in', 'Zoom In')]:
            desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
            menu = Node(AXRole='AXMenuItem', AXTitle=title)
            first = Node(AXRole='AXRadioButton', AXTitle='YouTube', AXSelected=False)
            second = Node(AXRole='AXRadioButton', AXTitle='Example', AXSelected=True)
            group = Node(AXRole='AXTabGroup', AXTabs=[first, second], AXChildren=[first, second], AXParent=ax.window)
            zoom = Node(AXRole='AXButton', AXDescription='Zoom', AXValue='125%', AXParent=ax.window)
            ax.window.attrs['AXChildren'].extend([group, zoom])
            ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu])
            ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node in {first, menu} else [])
            effects = []
            def press(node, action):
                effects.append(node)
                if command == 'close_tab':
                    group.attrs.update(AXTabs=[first], AXChildren=[first])
                    first.attrs['AXSelected'] = True
                elif command == 'zoom_in':
                    zoom.attrs['AXValue'] = f"{float(zoom.attrs['AXValue'][:-1]) + 25}%"
                else:
                    first.attrs['AXSelected'], second.attrs['AXSelected'] = True, False
                return 0
            ax.AXUIElementPerformAction = press
            for _ in range(2 if command == 'zoom_in' else 1):
                result = run_goal(goal, desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + command, 'none')]), approve=lambda _: True)
                self.assertEqual(result['outcome'], 'completed', result)
                self.assertEqual(len(result['steps']), 1)
                records.append(result)
            self.assertEqual(len(effects), 2 if command == 'zoom_in' else 1)
            if command == 'select_tab':
                self.assertIs(effects[0], first)
        Path('.build/reported-safari-tabs-e2e.json').write_text(json.dumps(records, indent=2))

    def test_unsupported_workflows_never_activate_an_unrelated_app(self):
        records = []
        for goal in ('close vs code', 'open a new terminal window', 'create a new note in Notion and type "hello"'):
            desktop = FakeDesktop()
            desktop.workspace.installed.update({'notion.id', 'com.apple.Notes', 'com.apple.TextEdit'})
            desktop.applications = (('Notion', 'notion.id', ('notion',)),)
            result = run_goal(goal, desktop, client=FakeJev(operation='UNSUPPORTED', target='none'))
            self.assertEqual(result['outcome'], 'unsupported', result)
            self.assertFalse(result['effect_sent'])
            self.assertEqual(desktop.effects, 0)
            records.append(result)
        Path('.build/reported-unsupported-e2e.json').write_text(json.dumps(records, indent=2))

    def test_safari_other_window_cannot_prove_tab_closure_or_zoom(self):
        from app.verification import verify_action
        records = []
        for mode in ('zoom_other_window', 'close_other_window', 'closed_original_window', 'lost_permission'):
            desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
            command = 'zoom_in' if mode == 'zoom_other_window' else 'close_tab'
            menu = Node(AXRole='AXMenuItem', AXTitle='Zoom In' if command == 'zoom_in' else 'Close Tab', AXEnabled=True)
            ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu])
            ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
            old_window = ax.window
            zoom = Node(AXRole='AXButton', AXDescription='Zoom', AXValue='125%', AXParent=old_window)
            tabs = [Node(AXRole='AXRadioButton', AXSelected=index == 0) for index in range(3)]
            old_window.attrs['AXChildren'].extend([zoom, Node(AXRole='AXTabGroup', AXTabs=tabs)])
            def press(node, action):
                window = Node(AXRole='AXWindow', AXChildren=[Node(AXRole='AXButton', AXDescription='Zoom', AXValue='150%'), Node(AXRole='AXTabGroup', AXTabs=[tabs[0]])])
                ax.application.attrs.update(AXFocusedWindow=window, AXFocusedUIElement=window,
                                            AXWindows=[window] if mode == 'closed_original_window' else [old_window, window])
                if mode == 'lost_permission':
                    ax.trusted = False
                return 0
            ax.AXUIElementPerformAction = press
            with patch('app.runtime.verify_action', side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0.1, **kwargs)):
                result = run_goal('zoom in' if command == 'zoom_in' else 'close this tab', desktop,
                                  client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + command, 'none')]), approve=lambda _: True)
            self.assertEqual(result['outcome'] == 'completed', mode == 'closed_original_window', result)
            self.assertTrue(result['effect_sent'])
            records.append(result)
        Path('.build/reported-safari-window-e2e.json').write_text(json.dumps(records, indent=2))

    @unittest.skipUnless(os.environ.get('JEV_LIVE_JEV') == '1', 'Live Jev opt-in; native services remain synthetic.')
    def test_live_jev_reported_notes_paste_and_finder_progression(self):
        records = []
        for mode in ('notes', 'paste', 'finder'):
            if mode == 'notes':
                desktop, ax = workflow_setup('com.apple.Notes')
                desktop.workspace.bundle = 'com.apple.TextEdit'
                desktop.workspace.URLForApplicationWithBundleIdentifier_ = lambda bundle: bundle if bundle in {'com.apple.Notes', 'com.apple.TextEdit', 'com.apple.Safari', 'notion.id'} else None
                desktop.workspace.openApplicationAtURL_configuration_completionHandler_ = lambda url, config, callback: (setattr(desktop.workspace, 'bundle', url), callback(desktop.workspace, None))
                goal, expected = 'create a new note in Notes and type "hello world"', 'hello world'
            elif mode == 'paste':
                desktop, ax, _, _ = input_setup(bundle='com.apple.Notes', value='', focused=False)
                expected = 'clipboard fixture 🦊'
                desktop.interactions.pasteboard = SimpleNamespace(changeCount=lambda: 1, stringForType_=lambda kind: expected)
                menu = Node(AXRole='AXMenuItem', AXTitle='Paste', AXEnabled=True)
                ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu])
                ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
                ax.AXUIElementPerformAction = lambda node, action: (ax.apply_text(expected) or 0)
                goal = 'paste into this note'
            else:
                desktop, ax = workflow_setup('com.apple.finder')
                ax.window.attrs['AXChildren'].remove(ax.editor)
                original = ax.AXUIElementPerformAction
                def press(node, action):
                    result = original(node, action)
                    if node is ax.control:
                        ax.window.attrs['AXChildren'].insert(0, ax.editor)
                    return result
                ax.AXUIElementPerformAction = press
                goal, expected = 'search Finder for invoices', 'invoices'
            result = run_goal(goal, desktop, approve=lambda _: True)
            records.append({'case':mode, **result})
            Path('.build/live-reported-regressions-e2e.json').write_text(json.dumps({'evidence':'live Jev; synthetic native services only', 'runs':records}, indent=2))
            self.assertEqual(result['outcome'], 'completed', result.get('reason'))
            self.assertEqual(ax.editor.attrs['AXValue'], expected)


if __name__ == '__main__':
    unittest.main()
