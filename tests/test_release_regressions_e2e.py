"""Exercise release fixes through the CLI with synthetic native/audio/provider services."""

import io
import json
import os
from pathlib import Path
import tempfile
from threading import Event, Timer
import unittest
from unittest.mock import patch

from app.cli import main
from app.runtime import run_goal
from app.verification import verify_action
from test_interactions_e2e import input_setup, InteractionJev, InteractionQuartz
from test_request_e2e import CatalogDesktop
from test_speech_e2e import FakeSpeechModel
from test_typing_e2e import Jev, Node


class ReleaseRegressionsEndToEnd(unittest.TestCase):
    """Persist exact browser evidence, cancellable prompts and bounded choice requests."""

    def test_browser_commands_need_the_requested_effect(self):
        records = {}
        labels = {'next_tab': 'Show Next Tab', 'previous_tab': 'Show Previous Tab', 'new_tab': 'New Tab',
                  'back': 'Back', 'zoom_in': 'Zoom In', 'zoom_out': 'Zoom Out'}
        for command, label in labels.items():
            for mode in ('correct', 'focus_only', 'wrong_direction', 'single_tab'):
                if command in {'new_tab', 'back'} and mode == 'wrong_direction':
                    continue
                if mode == 'single_tab' and command != 'next_tab':
                    continue
                desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
                menu = Node(AXRole='AXMenuItem', AXTitle=label, AXEnabled=True)
                reset = Node(AXRole='AXMenuItem', AXTitle='Actual Size', AXEnabled=True)
                ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu, reset])
                tabs = [Node(AXRole='AXRadioButton', AXSelected=index == 0) for index in range(1 if mode == 'single_tab' else 3)]
                group = Node(AXRole='AXTabGroup', AXTabs=tabs)
                zoom = Node(AXRole='AXButton', AXDescription='Zoom', AXValue='125%')
                ax.window.attrs['AXChildren'].extend([group, zoom])
                ax.window.attrs['AXURL'] = 'https://example.com/current'
                ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node in (menu, reset) else [])
                presses = []

                def press(node, action):
                    presses.append(node)
                    ax.application.attrs['AXFocusedUIElement'] = ax.window
                    if mode not in {'focus_only', 'single_tab'}:
                        if command in {'next_tab', 'previous_tab'}:
                            index = 1 if (command == 'next_tab') == (mode == 'correct') else 2
                            for number, tab in enumerate(tabs):
                                tab.attrs['AXSelected'] = number == index
                        elif command == 'new_tab':
                            tabs.append(Node(AXRole='AXRadioButton', AXSelected=True))
                        elif command == 'back':
                            ax.window.attrs['AXURL'] = 'https://example.com/previous'
                        elif command.startswith('zoom_'):
                            zoom.attrs['AXValue'] = '150%' if (command == 'zoom_in') == (mode == 'correct') else '100%'
                            reset.attrs['AXEnabled'] = zoom.attrs['AXValue'] != '100%'
                    return 0

                ax.AXUIElementPerformAction = press
                goal = {'next_tab': 'next tab', 'previous_tab': 'previous tab', 'new_tab': 'open a new tab',
                        'back': 'go back', 'zoom_in': 'zoom in', 'zoom_out': 'zoom out'}[command]
                with patch('app.runtime.verify_action', side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0, **kwargs)):
                    result = run_goal(goal, desktop, client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + command, 'none')]))
                self.assertEqual(result['outcome'], 'completed' if mode == 'correct' else 'unverified', result)
                self.assertEqual(len(presses), 1)
                records[f'{command}_{mode}'] = result
        Path('.build/release-browser-e2e.json').write_text(json.dumps(records, indent=2))

    def test_webpage_tabs_and_zoom_widgets_do_not_replace_browser_controls(self):
        records = {}
        for command, title in [('next_tab', 'Show Next Tab'), ('zoom_in', 'Zoom In')]:
            for page_only in (False, True):
                desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
                menu = Node(AXRole='AXMenuItem', AXTitle=title, AXEnabled=True)
                ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu])
                ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
                tabs = [Node(AXRole='AXRadioButton', AXSelected=index == 0) for index in range(2)]
                page_tabs = [Node(AXRole='AXRadioButton', AXSelected=index == 0) for index in range(2)]
                zoom = Node(AXRole='AXButton', AXDescription='Zoom', AXValue='125%')
                page_zoom = Node(AXRole='AXButton', AXDescription='Zoom chart', AXValue='999%')
                ax.window.attrs['AXChildren'].extend([Node(AXRole='AXTabGroup', AXTabs=tabs), zoom,
                    Node(AXRole='AXWebArea', AXChildren=[Node(AXRole='AXTabGroup', AXTabs=page_tabs), page_zoom])])

                def press(node, action):
                    if command == 'next_tab':
                        selected = page_tabs if page_only else tabs
                        selected[0].attrs['AXSelected'], selected[1].attrs['AXSelected'] = False, True
                    else:
                        (page_zoom if page_only else zoom).attrs['AXValue'] = '150%'
                    return 0

                ax.AXUIElementPerformAction = press
                with patch('app.runtime.verify_action', side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0, **kwargs)):
                    result = run_goal('next tab' if command == 'next_tab' else 'zoom in', desktop,
                                      client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:' + command, 'none')]))
                self.assertEqual(result['outcome'], 'unverified' if page_only else 'completed', result)
                self.assertEqual(result['editor_readiness']['interaction_discovery']['tab_count'], 2)
                records[f'{command}_{"page_only" if page_only else "browser"}'] = result
        Path('.build/release-browser-content-e2e.json').write_text(json.dumps(records, indent=2))

    def test_scroll_needs_directional_movement_without_retry(self):
        records = {}
        for mode in ('correct', 'focus_only', 'wrong_direction'):
            desktop, ax, _, _ = input_setup(bundle='com.apple.Safari')
            ax.kAXValueCGPointType, ax.kAXValueCGSizeType = 1, 2
            quartz = InteractionQuartz(ax)
            desktop.text.quartz = quartz
            quartz.bar = Node(AXRole='AXScrollBar', AXValue=0.4)
            area = Node(AXRole='AXScrollArea', AXPosition=(0, 0), AXSize=(800, 500), AXVerticalScrollBar=quartz.bar)
            ax.window.attrs['AXChildren'].insert(0, area)

            def post(pid, event):
                quartz.events.append(event)
                ax.application.attrs['AXFocusedUIElement'] = ax.window
                quartz.bar.attrs['AXValue'] = {'correct': 0.7, 'focus_only': 0.4, 'wrong_direction': 0.1}[mode]

            quartz.CGEventPostToPid = post
            with patch('app.runtime.verify_action', side_effect=lambda *args, **kwargs: verify_action(*args, timeout_seconds=0, **kwargs)):
                result = run_goal('scroll down', desktop, client=InteractionJev(plan=[('SCROLL', 'action:scroll_down', 'none')]))
            self.assertEqual(result['outcome'], 'completed' if mode == 'correct' else 'unverified', result)
            self.assertEqual(len(quartz.events), 1)
            records[mode] = result
        Path('.build/release-scroll-e2e.json').write_text(json.dumps(records, indent=2))

    def test_voice_stop_cancels_terminal_approval_without_a_keyboard_answer(self):
        records = {}
        for mode in ('stop', 'approve', 'reject', 'capture_error'):
            desktop, ax, _, _ = input_setup(value='hello', selection=(0, 5))
            menu = Node(AXRole='AXMenuItem', AXTitle='Cut', AXEnabled=True)
            ax.application.attrs['AXMenuBar'] = Node(AXRole='AXMenuBar', AXChildren=[menu])
            ax.AXUIElementCopyActionNames = lambda node, unused: (0, ['AXPress'] if node is menu else [])
            clipboard = [1]
            desktop.interactions.pasteboard = type('Clipboard', (), {'changeCount': lambda _: clipboard[0]})()

            def press(node, action):
                clipboard[0] += 1
                ax.effects.append(('press', 'Cut'))
                ax.apply_text('')
                return 0

            ax.AXUIElementPerformAction = press
            prompt_started, forced_answer = Event(), Event()

            class Output(io.StringIO):
                def write(self, value):
                    result = super().write(value)
                    if 'Confirm spoken action:' in value:
                        prompt_started.set()
                    return result

            master, slave = os.openpty()
            stream = os.fdopen(slave, 'r')

            def capture(model, on_final, **kwargs):
                on_final('cut')
                if not prompt_started.wait(2):
                    raise AssertionError('The mandatory cut approval never appeared')
                if mode == 'stop':
                    on_final('stop listening')
                elif mode == 'capture_error':
                    raise RuntimeError('Synthetic microphone failure during approval')
                else:
                    os.write(master, b'y\n' if mode == 'approve' else b'n\n')
                return 2 if mode == 'stop' else 1

            def unblock_failed_probe():
                forced_answer.set()
                os.write(master, b'n\n')

            watchdog = Timer(3, unblock_failed_probe)
            try:
                with tempfile.TemporaryDirectory() as directory, stream, \
                        patch('app.cli.sys.stdin', stream), \
                        patch('app.speech_runtime.run_live_asr', side_effect=capture):
                    path = Path(directory) / 'voice.json'
                    watchdog.start()
                    output = Output()
                    main(['--listen', '--yes', '--report', str(path)], desktop=desktop,
                         client=InteractionJev(plan=[('KEYBOARD_ACTION', 'action:cut', 'none')]),
                         output=output, speech_model=FakeSpeechModel())
                    report = json.loads(path.read_text())
            finally:
                watchdog.cancel()
                watchdog.join()
                os.close(master)
            self.assertFalse(forced_answer.is_set(), 'Cancellation still needed a keyboard answer')
            result = report['runs'][0]
            self.assertEqual(result['outcome'], {'stop': 'cancelled', 'capture_error': 'cancelled', 'approve': 'completed', 'reject': 'rejected'}[mode], result)
            self.assertEqual(result['effect_sent'], mode == 'approve')
            self.assertEqual(len(ax.effects), int(mode == 'approve'))
            if mode in {'stop', 'capture_error'}:
                self.assertNotIn('Still listening', output.getvalue())
            records[mode] = report
        Path('.build/release-approval-stop-e2e.json').write_text(json.dumps(records, indent=2))

    def test_large_catalog_typing_and_working_web_queries(self):
        records = {}
        for mode, goal in [('typing', 'type "hello world"'), ('named_typing', 'type "hello world" in TextEdit'),
                           ('named_background', 'type "hello world" in Notes'),
                           ('google', 'google messi soccer videos'),
                           ('youtube', 'search for messi soccer videos on youtube')]:
            desktop, ax, _, workspace = input_setup(value='')
            catalog = CatalogDesktop(340)
            desktop.applications = catalog.applications
            installed = catalog.workspace.installed | {'com.apple.TextEdit'}
            workspace.URLForApplicationWithBundleIdentifier_ = lambda bundle: object() if bundle in installed else None
            if mode in {'typing', 'named_typing'}:
                plan = [('TYPE_TEXT', 'editor', 'span_0')]
            elif mode == 'named_background':
                plan = [('OPEN_APP_FOR_TEXT', 'com.apple.Notes', 'span_0')]
            else:
                payload = next(key for key, facts in desktop.semantic_menu(goal, desktop.snapshot()).payloads.items()
                               if facts.get('text') == 'messi soccer videos')
                plan = [('SEARCH_WEB', f'web:{mode}:com.apple.Safari', payload)]

            class Client(Jev):
                def system_one(self, state, questions, **kwargs):
                    self.option_counts = {key: len(question.criteria) for key, question in questions.items()}
                    return super().system_one(state, questions, **kwargs)

            client = Client(plan=plan)
            result = run_goal(goal, desktop, client=client, dry_run=mode not in {'typing', 'named_typing'})
            self.assertEqual(result['outcome'], 'completed' if mode in {'typing', 'named_typing'} else 'dry_run', result)
            self.assertLessEqual(max(client.option_counts.values()), 255)
            self.assertIn('com.example.application335', result['target_ids'])
            if mode in {'typing', 'named_typing'}:
                self.assertEqual(ax.editor.attrs['AXValue'], 'hello world')
            elif mode in {'google', 'youtube'}:
                self.assertEqual(result['decision']['selected_candidate']['parameters']['url'],
                                 'https://www.google.com/search?q=messi+soccer+videos' if mode == 'google' else
                                 'https://www.youtube.com/results?search_query=messi+soccer+videos')
                self.assertFalse(ax.effects)
            records[mode] = result
        Path('.build/release-catalog-e2e.json').write_text(json.dumps(records, indent=2))
