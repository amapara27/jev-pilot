"""Replay native AX omissions from user logs through guarded effects and verification."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from AppKit import NSAttributedString
from app.runtime import run_goal
from app.verification import verify_action
from test_typing_e2e import Jev, Node
from test_workflow_e2e import setup as workflow_setup
from test_interactions_e2e import input_setup, InteractionJev, InteractionQuartz


def native_menu(ax, items):
    """Expose an AppKit menu whose optional subrole produces the reported AX failure."""

    menu = Node(AXRole='AXMenuBar', AXChildren=items, AXParent=ax.application)
    for item in items:
        item.attrs['AXParent'] = menu
    ax.application.attrs['AXMenuBar'] = menu
    read = ax.AXUIElementCopyAttributeValue
    ax.AXUIElementCopyAttributeValue = lambda node, attribute, unused: (-25200, None) if node in (menu, ax.application) and attribute == 'AXSubrole' else read(node, attribute, unused)
    return menu


class NativeDiscoveryEndToEnd(unittest.TestCase):
    """Keep exact text and native identity guards while exercising real failure shapes."""

    def test_notes_nil_and_attributed_values_still_create_then_type_once(self):
        records = []
        for mode in ('nil_empty', 'attributed', 'range', 'unreadable', 'oversized', 'range_mismatch', 'secure'):
            desktop, ax = workflow_setup('com.apple.Notes')
            original_read = ax.AXUIElementCopyAttributeValue
            ax.AXValueCreate = lambda kind, value: value
            ax.AXUIElementCopyParameterizedAttributeValue = lambda node, name, value, unused: (0, ax.editor.attrs['AXValue'] + ('extra' if mode == 'range_mismatch' else ''))
            def read(node, attribute, unused):
                if node is ax.editor and attribute == 'AXValue':
                    if mode in {'unreadable', 'range_mismatch', 'nil_empty'}:
                        return 0, None
                    if mode == 'range':
                        return -25205, None
                    if mode == 'attributed':
                        return 0, NSAttributedString.alloc().initWithString_(ax.editor.attrs['AXValue'])
                if node is ax.editor and attribute == 'AXNumberOfCharacters':
                    if mode == 'unreadable':
                        return -25204, None
                    return 0, 200000 if mode == 'oversized' else len(ax.editor.attrs['AXValue'].encode('utf-16-le')) // 2
                return original_read(node, attribute, unused)
            if mode == 'oversized':
                ax.editor.attrs['AXValue'] = None
            ax.AXUIElementCopyAttributeValue = read
            if mode == 'secure':
                ax.editor.attrs['AXSubrole'] = 'AXSecureTextField'
            if mode in {'unreadable', 'range_mismatch', 'oversized', 'secure'}:
                goal = 'type "hello world"'
                plan = [('TYPE_TEXT', 'editor', 'span_0')]
            else:
                goal = 'create a new note and type hello world.'
                plan = [('CREATE_NOTE', 'com.apple.Notes', 'span_0'), ('TYPE_TEXT', 'editor', 'span_0')]
            result = run_goal(goal, desktop, client=Jev(plan=plan), approve=lambda _: True)
            self.assertEqual(result['outcome'] == 'completed', mode in {'nil_empty', 'attributed', 'range'}, result)
            if mode in {'nil_empty', 'attributed', 'range'}:
                self.assertEqual(ax.editor.attrs['AXValue'], 'hello world.')
                self.assertEqual(sum(item['decision']['semantic']['operation'] == 'TYPE_TEXT' for item in result['steps']), 1)
            else:
                self.assertFalse(result['effect_sent'])
                self.assertFalse(ax.effects)
            records.append(result)
        Path('.build/native-text-discovery-e2e.json').write_text(json.dumps(records, indent=2))

    def test_missing_focus_does_not_hide_safari_menus_or_scroll_area(self):
        records = []
        for command in ('next_tab', 'back', 'scroll_down'):
            desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
            ax.application.attrs.pop('AXFocusedUIElement')
            menu = Node(AXRole='AXMenuItem', AXTitle='Show Next Tab' if command == 'next_tab' else 'Back', AXEnabled=True)
            native_menu(ax, [menu])
            ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
            tabs = [Node(AXRole='AXRadioButton', AXSelected=True), Node(AXRole='AXRadioButton', AXSelected=False)]
            ax.window.attrs['AXChildren'].append(Node(AXRole='AXTabGroup', AXTabs=tabs))
            ax.window.attrs['AXURL'] = 'https://example.com/two'
            def press(node, action):
                if command == 'next_tab':
                    tabs[0].attrs['AXSelected'], tabs[1].attrs['AXSelected'] = False, True
                else:
                    ax.window.attrs['AXURL'] = 'https://example.com/one'
                return 0
            ax.AXUIElementPerformAction = press
            if command == 'scroll_down':
                ax.kAXValueCGPointType, ax.kAXValueCGSizeType = 1, 2
                quartz = InteractionQuartz(ax)
                desktop.text.quartz = quartz
                quartz.bar = Node(AXRole='AXScrollBar', AXValue=0.3)
                area = Node(AXRole='AXScrollArea', AXPosition=(0, 0), AXSize=(800, 500), AXVerticalScrollBar=quartz.bar)
                ax.window.attrs['AXChildren'].insert(0, area)
            result = run_goal({'next_tab':'switch to next tab', 'back':'go back', 'scroll_down':'scroll down.'}[command], desktop,
                              client=InteractionJev(plan=[('SCROLL' if command == 'scroll_down' else 'KEYBOARD_ACTION', 'action:' + command, 'none')]))
            self.assertEqual(result['outcome'], 'completed', result)
            self.assertIn('interaction_discovery', result['editor_readiness'])
            records.append(result)
        Path('.build/native-safari-discovery-e2e.json').write_text(json.dumps(records, indent=2))

    def test_created_note_waits_for_readable_body_without_replaying_creation(self):
        records = []
        for mode in ('delayed', 'unavailable', 'note_drift'):
            desktop, ax = workflow_setup('com.apple.Notes')
            row = Node(AXRole='AXRow', AXSelected=True)
            table = Node(AXRole='AXTable', AXSelectedRows=[row], AXChildren=[row], AXParent=ax.window)
            row.attrs['AXParent'] = table
            ax.window.attrs['AXChildren'].append(table)
            created, reads = [False], [0]
            press = ax.AXUIElementPerformAction
            def create(node, action):
                result = press(node, action)
                if node is ax.control:
                    created[0] = True
                    row.attrs['AXSelected'] = False
                    new_row = Node(AXRole='AXRow', AXSelected=True, AXParent=table)
                    table.attrs.update(AXSelectedRows=[new_row], AXChildren=[row, new_row])
                return result
            ax.AXUIElementPerformAction = create
            original_read = ax.AXUIElementCopyAttributeValue
            def read(node, attribute, unused):
                if created[0] and node is ax.editor and attribute == 'AXValue':
                    reads[0] += 1
                    if mode == 'note_drift' and reads[0] == 3:
                        table.attrs['AXSelectedRows'] = [row]
                        for item in table.attrs['AXChildren']:
                            item.attrs['AXSelected'] = item is row
                    if mode != 'delayed' or reads[0] < 4:
                        return 0, None
                if created[0] and node is ax.editor and attribute == 'AXNumberOfCharacters':
                    return -25212, None
                return original_read(node, attribute, unused)
            ax.AXUIElementCopyAttributeValue = read
            client = Jev(plan=[('CREATE_NOTE', 'com.apple.Notes', 'span_0'), ('TYPE_TEXT', 'editor', 'span_0')])
            if mode == 'unavailable':
                with patch('app.runtime.monotonic', side_effect=[0, 1, 5]):
                    result = run_goal('create a new note and type "hello world"', desktop, client=client)
            else:
                result = run_goal('create a new note and type "hello world"', desktop, client=client)
            self.assertEqual(result['outcome'] == 'completed', mode == 'delayed', result)
            self.assertEqual(sum(effect == ('press', 'New Note') for effect in ax.effects), 1)
            self.assertTrue(result['steps'][0]['effect_sent'])
            if mode == 'delayed':
                self.assertEqual(ax.editor.attrs['AXValue'], 'hello world')
                self.assertEqual(len(client.calls), 2)
            else:
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(ax.editor.attrs['AXValue'], '')
            records.append(result)
        Path('.build/native-note-readiness-e2e.json').write_text(json.dumps(records, indent=2))

    def test_native_menu_omission_does_not_hide_paste_cut_or_finder_search(self):
        records = []
        for mode in ('paste', 'cut', 'finder', 'secure_parent'):
            if mode == 'finder':
                desktop, ax = workflow_setup('com.apple.finder')
                ax.window.attrs['AXChildren'].remove(ax.editor)
                native_menu(ax, [ax.control, ax.open_control])
                press = ax.AXUIElementPerformAction
                def find(node, action):
                    error = press(node, action)
                    if node is ax.control:
                        ax.window.attrs['AXChildren'].insert(0, ax.editor)
                    return error
                ax.AXUIElementPerformAction = find
                goal = 'Search for Resumes in Finder'
                client = Jev(plan=[('PREPARE_SEARCH', 'com.apple.finder', 'span_1'), ('SEARCH', 'com.apple.finder', 'span_1')])
            else:
                desktop, ax, _, _ = input_setup(bundle='com.apple.Notes', value='hello', selection=(0, 5))
                menu = Node(AXRole='AXMenuItem', AXTitle='Cut' if mode == 'cut' else 'Paste', AXEnabled=True)
                bar = native_menu(ax, [menu])
                if mode == 'secure_parent':
                    bar.attrs['AXProtectedContent'] = True
                ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
                count = [1]
                desktop.interactions.pasteboard = SimpleNamespace(changeCount=lambda: count[0], stringForType_=lambda kind: 'pasted 🦊')
                def press(node, action):
                    ax.apply_text('' if mode == 'cut' else 'pasted 🦊')
                    count[0] += 1
                    return 0
                ax.AXUIElementPerformAction = press
                goal = 'cut' if mode == 'cut' else 'paste'
                client = InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + goal, 'none')])
            result = run_goal(goal, desktop, client=client, approve=lambda _: True)
            self.assertEqual(result['outcome'] == 'completed', mode != 'secure_parent', result)
            if mode != 'secure_parent':
                self.assertEqual(ax.editor.attrs['AXValue'], '' if mode == 'cut' else 'Resumes' if mode == 'finder' else 'pasted 🦊')
            else:
                self.assertFalse(result['effect_sent'])
            records.append(result)
        Path('.build/native-menu-discovery-e2e.json').write_text(json.dumps(records, indent=2))

    def test_fullscreen_and_minimize_target_the_original_window(self):
        records = []
        for command in ('maximize', 'minimize'):
            for mode in ('normal', 'window_drift', 'permission', 'ignored', 'setter_error'):
                desktop, ax, _, _ = input_setup()
                ax.application.attrs.pop('AXFocusedUIElement')
                attribute = 'AXFullScreen' if command == 'maximize' else 'AXMinimized'
                window = ax.window
                window.attrs[attribute] = False
                window.writable.add(attribute)
                ax.application.attrs['AXWindows'] = [window]
                setter = ax.AXUIElementSetAttributeValue
                effects = []
                def set_value(node, name, value):
                    if name == attribute:
                        effects.append((node, name, value))
                        if mode == 'setter_error':
                            return -25204
                        if mode != 'ignored':
                            node.attrs[name] = value
                        return 0
                    return setter(node, name, value)
                ax.AXUIElementSetAttributeValue = set_value
                def drift():
                    if mode == 'window_drift':
                        ax.application.attrs['AXFocusedWindow'] = Node(AXRole='AXWindow', **{attribute: False})
                    if mode == 'permission':
                        ax.trusted = False
                with patch('app.runtime.verify_action', side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0.1, **kwargs)):
                    result = run_goal(command + ' this window', desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + command, 'none')], after_call=drift))
                self.assertEqual(result['outcome'] == 'completed', mode == 'normal', result)
                self.assertEqual(len(effects), 0 if mode in {'window_drift', 'permission'} else 1)
                if mode == 'normal':
                    self.assertTrue(window.attrs[attribute])
                records.append(result)
        Path('.build/native-window-actions-e2e.json').write_text(json.dumps(records, indent=2))

    def test_named_window_actions_and_terminal_window_controls(self):
        records = []
        for bundle in ('com.apple.TextEdit', 'com.apple.Terminal'):
            desktop, ax, _, _ = input_setup(bundle=bundle)
            desktop.workspace.URLForApplicationWithBundleIdentifier_ = lambda identifier: object() if identifier in {'com.apple.TextEdit', 'com.apple.Terminal', 'com.apple.Safari'} else None
            ax.window.attrs.update(AXFullScreen=False, AXMinimized=False)
            ax.window.writable.update({'AXFullScreen', 'AXMinimized'})
            ax.application.attrs['AXWindows'] = [ax.window]
            ax.AXUIElementSetAttributeValue = lambda node, attribute, value: (node.attrs.update({attribute:value}) or 0)
            name = 'TextEdit' if bundle == 'com.apple.TextEdit' else 'Terminal'
            for command in ('maximize', 'minimize'):
                result = run_goal(command + ' ' + name, desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + command, 'none')]))
                self.assertEqual(result['outcome'], 'completed', result)
                self.assertTrue(ax.window.attrs['AXFullScreen' if command == 'maximize' else 'AXMinimized'])
                records.append(result)
        desktop, ax, _, _ = input_setup()
        ax.window.attrs['AXFullScreen'] = False
        ax.window.writable.add('AXFullScreen')
        result = run_goal('maximize Safari', desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:maximize', 'none')]))
        self.assertFalse(result['effect_sent'])
        self.assertFalse(ax.window.attrs['AXFullScreen'])
        records.append(result)
        Path('.build/native-named-window-e2e.json').write_text(json.dumps(records, indent=2))

    def test_explicit_window_tail_follows_one_insertion(self):
        records = []
        for command in ('maximize', 'minimize'):
            desktop, ax, _, _ = input_setup(value='')
            attribute = 'AXFullScreen' if command == 'maximize' else 'AXMinimized'
            ax.window.attrs[attribute] = False
            ax.window.writable.add(attribute)
            ax.application.attrs['AXWindows'] = [ax.window]
            setter = ax.AXUIElementSetAttributeValue
            def set_value(node, name, value):
                return (node.attrs.update({name:value}) or 0) if name == attribute else setter(node, name, value)
            ax.AXUIElementSetAttributeValue = set_value
            result = run_goal('type hello world and ' + command + ' this window', desktop, client=InteractionJev(
                plan=[('TYPE_TEXT', 'editor', 'span_1'), ('KEYBOARD_ACTION', 'action:' + command, 'none')]))
            self.assertEqual(result['outcome'], 'completed', result)
            self.assertEqual(ax.editor.attrs['AXValue'], 'hello world')
            self.assertTrue(ax.window.attrs[attribute])
            records.append(result)
        desktop, ax, _, _ = input_setup()
        result = run_goal('maximizer', desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:maximize', 'none')]))
        self.assertFalse(result['effect_sent'])
        records.append(result)
        Path('.build/native-window-tail-e2e.json').write_text(json.dumps(records, indent=2))

    @unittest.skipUnless(os.environ.get('JEV_LIVE_JEV') == '1', 'Live Jev opt-in; desktop services stay synthetic.')
    def test_live_jev_progresses_with_nil_notes_and_missing_safari_focus(self):
        records = []
        for mode in ('notes', 'next_tab', 'maximize'):
            if mode == 'notes':
                desktop, ax = workflow_setup('com.apple.Notes')
                read = ax.AXUIElementCopyAttributeValue
                ax.AXValueCreate = lambda kind, value: value
                ax.AXUIElementCopyParameterizedAttributeValue = lambda node, name, value, unused: (0, ax.editor.attrs['AXValue'])
                def value(node, attribute, unused):
                    if node is ax.editor and attribute == 'AXValue':
                        return 0, None
                    if node is ax.editor and attribute == 'AXNumberOfCharacters':
                        return 0, len(ax.editor.attrs['AXValue'])
                    return read(node, attribute, unused)
                ax.AXUIElementCopyAttributeValue = value
                goal = 'create a new note and type hello world.'
            else:
                desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
                ax.application.attrs.pop('AXFocusedUIElement')
                if mode == 'next_tab':
                    menu = Node(AXRole='AXMenuItem', AXTitle='Show Next Tab', AXEnabled=True)
                    native_menu(ax, [menu])
                    ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
                    tabs = [Node(AXRole='AXRadioButton', AXSelected=True), Node(AXRole='AXRadioButton', AXSelected=False)]
                    ax.window.attrs['AXChildren'].append(Node(AXRole='AXTabGroup', AXTabs=tabs))
                    ax.AXUIElementPerformAction = lambda node, action: (tabs[0].attrs.update(AXSelected=False), tabs[1].attrs.update(AXSelected=True), 0)[-1]
                    goal = 'switch to next tab'
                else:
                    ax.window.attrs['AXFullScreen'] = False
                    ax.window.writable.add('AXFullScreen')
                    ax.application.attrs['AXWindows'] = [ax.window]
                    ax.AXUIElementSetAttributeValue = lambda node, attribute, value: (node.attrs.update({attribute:value}) or 0)
                    goal = 'maximize this window'
            result = run_goal(goal, desktop, approve=lambda _: True)
            records.append(result)
            Path('.build/live-native-discovery-e2e.json').write_text(json.dumps({'evidence':'live Jev; synthetic native services only', 'runs':records}, indent=2))
            self.assertEqual(result['outcome'], 'completed', result.get('reason'))
            if mode == 'notes':
                self.assertEqual(ax.editor.attrs['AXValue'], 'hello world.')


if __name__ == '__main__':
    unittest.main()
