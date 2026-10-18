"""Replay native app-handle and scrollbar omissions without real desktop effects."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.runtime import run_goal
from app.verification import verify_action
from test_interactions_e2e import input_setup, InteractionJev, InteractionQuartz
from test_typing_e2e import Jev, Node


class NativeControlsEndToEnd(unittest.TestCase):
    """Keep process identity, scroll ranges, and unavailable-command boundaries exact."""

    def test_invalid_safari_application_uses_only_a_matching_system_focused_app(self):
        cases = {}
        for command, title in [('next_tab', 'Show Next Tab'), ('new_tab', 'New Tab'), ('zoom_in', 'Zoom In'), ('scroll_down', '')]:
            for mode in ('matching', 'matching_window_only', 'wrong_pid', 'wrong_pid_window_only', 'pid_error', 'unreadable_focused_app'):
                desktop, ax, _, workspace = input_setup(bundle='com.apple.Safari')
                direct = Node(AXRole='AXApplication')
                system = Node(AXFocusedApplication=ax.application)
                if mode == 'unreadable_focused_app':
                    system.attrs.clear()
                ax.AXUIElementCreateApplication = lambda pid: direct
                ax.AXUIElementCreateSystemWide = lambda: system
                ax.AXUIElementGetPid = lambda app, unused: (-25202, 0) if mode == 'pid_error' else (0, 999 if mode.startswith('wrong_pid') else workspace.pid)
                read = ax.AXUIElementCopyAttributeValue
                ax.AXUIElementCopyAttributeValue = lambda node, name, unused: (-25202, None) if node is direct and (not mode.endswith('window_only') or name == 'AXFocusedWindow') else read(node, name, unused)
                menu = Node(AXRole='AXMenuItem', AXTitle=title, AXEnabled=True)
                ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu])
                ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
                tabs = [Node(AXRole='AXRadioButton', AXSelected=index == 0) for index in range(2)]
                group = Node(AXRole='AXTabGroup', AXTabs=tabs)
                zoom = Node(AXRole='AXButton', AXDescription='Zoom', AXValue='125%')
                ax.window.attrs['AXChildren'].extend([group, zoom])
                effects = []

                def press(node, action):
                    effects.append(command)
                    if command == 'next_tab':
                        tabs[0].attrs['AXSelected'], tabs[1].attrs['AXSelected'] = False, True
                    elif command == 'new_tab':
                        tabs.append(Node(AXRole='AXRadioButton', AXSelected=False))
                    else:
                        zoom.attrs['AXValue'] = '150%'
                    return 0

                ax.AXUIElementPerformAction = press
                if command == 'scroll_down':
                    ax.kAXValueCGPointType, ax.kAXValueCGSizeType = 1, 2
                    quartz = InteractionQuartz(ax)
                    desktop.text.quartz = quartz
                    quartz.bar = Node(AXRole='AXScrollBar', AXValue=0.3)
                    ax.window.attrs['AXChildren'].insert(0, Node(AXRole='AXScrollArea', AXPosition=(0, 0), AXSize=(800, 500), AXVerticalScrollBar=quartz.bar))
                with patch('app.runtime.verify_action', side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0, **kwargs)):
                    result = run_goal({'next_tab': 'next tab', 'new_tab': 'open a new tab', 'zoom_in': 'zoom in', 'scroll_down': 'scroll down'}[command], desktop,
                                      client=InteractionJev(plan=[('SCROLL' if command == 'scroll_down' else 'KEYBOARD_ACTION', 'action:' + command, 'none')]))
                self.assertEqual(result['outcome'] == 'completed', mode.startswith('matching'), result)
                self.assertEqual(len(desktop.text.quartz.events) if command == 'scroll_down' else len(effects), int(mode.startswith('matching')))
                cases[f'{command}_{mode}'] = result
        Path('.build/native-application-handle-e2e.json').write_text(json.dumps(cases, indent=2))

    def test_finder_standard_scrollbars_and_diagnostic_rejections(self):
        cases = {}
        for mode in ('normalized', 'read_error', 'out_of_range', 'not_a_scrollbar', 'readonly'):
            desktop, ax, _, _ = input_setup(bundle='com.apple.finder')
            ax.kAXValueCGPointType, ax.kAXValueCGSizeType = 1, 2
            bar = Node(AXRole='AXSlider' if mode == 'not_a_scrollbar' else 'AXScrollBar', AXValue=2 if mode == 'out_of_range' else 0.2)
            if mode != 'readonly':
                bar.writable.add('AXValue')
            ax.window.attrs['AXChildren'].insert(0, Node(AXRole='AXScrollArea', AXPosition=(0, 0), AXSize=(600, 500), AXVerticalScrollBar=bar))
            read = ax.AXUIElementCopyAttributeValue
            ax.AXUIElementCopyAttributeValue = lambda node, name, unused: (-25204, None) if mode == 'read_error' and node is bar and name in {'AXMinValue', 'AXMaxValue'} else read(node, name, unused)
            setter = ax.AXUIElementSetAttributeValue
            ax.AXUIElementSetAttributeValue = lambda node, name, value: (ax.effects.append((name, value)) or bar.attrs.update(AXValue=value) or 0) if node is bar else setter(node, name, value)
            result = run_goal('go to the bottom', desktop, client=InteractionJev(plan=[('SCROLL', 'action:scroll_bottom', 'none')]))
            self.assertEqual(result['outcome'] == 'completed', mode == 'normalized', result)
            self.assertEqual(len(ax.effects), int(mode == 'normalized'))
            self.assertIn('scrollbar_checks', result['editor_readiness']['interaction_discovery'])
            cases[mode] = result
        Path('.build/finder-scrollbar-e2e.json').write_text(json.dumps(cases, indent=2))

    def test_unavailable_scroll_cannot_focus_an_unrelated_editor(self):
        desktop, ax, _, _ = input_setup(bundle='notion.id', focused=False)
        result = run_goal('scroll down', desktop, client=Jev(plan=[('FOCUS_FIELD', 'editor', 'none')]))
        self.assertFalse(result['effect_sent'], result)
        self.assertEqual(ax.effects, [])
        Path('.build/unavailable-scroll-e2e.json').write_text(json.dumps(result, indent=2))
