"""Ground explicit keyboard commands and wheel events in current native context."""

from collections import deque
from dataclasses import dataclass, field
import math
import re
from time import monotonic

from AppKit import NSPasteboard
from Quartz import kCGEventFlagMaskShift

from .text_input import TextInputError, expected_insertion, native_true, utf16_length


# Menu actions must exist and be enabled in the foreground app.
MENU_LABELS = {
    'select_all': {'select all'},
    'copy': {'copy'}, 'cut': {'cut'}, 'paste': {'paste'},
    'undo': {'undo'}, 'redo': {'redo'}, 'find': {'find', 'find…', 'find...'},
    'new_tab': {'new tab'}, 'close_tab': {'close tab'},
    'reopen_tab': {'reopen closed tab', 'reopen last closed tab'},
    'next_tab': {'show next tab', 'select next tab'},
    'previous_tab': {'show previous tab', 'select previous tab'},
    'back': {'back'}, 'forward': {'forward'}, 'reload': {'reload', 'reload page'},
    'address_bar': {'open location', 'open location…', 'open location...'},
    'zoom_in': {'zoom in'}, 'zoom_out': {'zoom out'}, 'reset_zoom': {'actual size'},
}
TEXT_COMMANDS = {'select_all', 'copy', 'cut', 'paste', 'undo', 'redo', 'enter'}
BROWSER_COMMANDS = set(MENU_LABELS) - TEXT_COMMANDS - {'find'}
PATTERNS = [
    ('enter', r'(?:press|hit)\s+(?:the\s+)?(?:enter|return)(?:\s+key)?'),
    ('select_all', r'select\s+all(?:\s+text)?'),
    ('reopen_tab', r'reopen\s+(?:the\s+)?(?:last\s+)?(?:closed\s+)?tab'),
    ('close_tab', r'close\s+(?:(?:this|the|current)\s+)?tab'),
    ('new_tab', r'(?:open\s+)?(?:a\s+)?new\s+tab'),
    ('next_tab', r'(?:go\s+to\s+)?(?:the\s+)?next\s+tab'),
    ('previous_tab', r'(?:go\s+to\s+)?(?:the\s+)?previous\s+tab'),
    ('back', r'go\s+back'), ('forward', r'go\s+forward'),
    ('address_bar', r'(?:focus|select)\s+(?:the\s+)?address\s+bar'),
    ('zoom_in', r'zoom\s+in'), ('zoom_out', r'zoom\s+out'),
    ('reset_zoom', r'reset\s+zoom'), ('reload', r'(?:reload|refresh)(?:\s+(?:this|the)\s+page)?'),
    ('find', r'(?:find\s+(?:on|in)\s+(?:this|the)\s+page|open\s+(?:the\s+)?find)'),
    ('copy', r'copy'), ('cut', r'cut'), ('paste', r'paste'), ('undo', r'undo'), ('redo', r'redo'),
    ('escape', r'(?:press|hit)\s+(?:the\s+)?escape(?:\s+key)?'),
    ('shift_tab', r'(?:press|hit)\s+shift\s+tab'), ('tab', r'(?:press|hit)\s+tab'),
    ('scroll_top', r'(?:scroll|go)\s+to\s+(?:the\s+)?top'),
    ('scroll_bottom', r'(?:scroll|go)\s+to\s+(?:the\s+)?bottom'),
    ('scroll', r'scroll\s+(up|down|left|right)(?:\s+(a\s+lot|a\s+little|one\s+page|a\s+page))?'),
]
QUOTES = r'"(?:\\.|[^"\\])*"|“[^”]*”|(?<!\w)\x27[^\x27]*\x27(?!\w)'


def command_actions(goal, *, with_positions=False):
    """Find explicit ordered commands, shielding quotes and dictated/search text."""

    masked = re.sub(QUOTES, lambda m: ' ' * len(m.group()), goal)
    literal = re.search(r'\b(?:type|write|dictate|insert|google|search|find(?!\s+(?:on|in)\s+(?:this|the)\s+page)|locate)\s+', masked, re.I)
    if literal:
        tail = masked[literal.end():]
        # shortcut: unquoted command tails need an explicit connector; quote literal tails.
        boundary = re.search(r'\s+(?:and(?:\s+then)?|then)\s+(?=(?:press|hit|select all|scroll|go to the|copy\b|cut\b|paste\b|undo\b|redo\b|close\b|reload\b|zoom\b))', tail, re.I) if literal.group().strip().casefold() in {'type', 'write', 'dictate', 'insert'} else None
        masked = masked[:literal.start()] + ' ' * ((literal.end() + boundary.end() if boundary else len(masked)) - literal.start()) + (tail[boundary.end():] if boundary else '')
    matches = []
    for command, pattern in PATTERNS:
        for match in re.finditer(r'\b' + pattern + r'\b', masked, re.I):
            name = command
            if command == 'scroll':
                name = 'scroll_' + match.group(1).lower()
                amount = (match.group(2) or '').lower()
                name += '_large' if amount in {'a lot', 'one page', 'a page'} else '_small' if amount else ''
            matches.append((match.start(), match.end(), name))
    matches.sort(key=lambda m: (m[0], -(m[1] - m[0])))
    result, end = [], -1
    for start, stop, name in matches:
        if start >= end:
            result.append((start, name) if with_positions else name)
            end = stop
    return result


def action_operation(command):
    return 'SCROLL' if command.startswith('scroll_') else 'KEYBOARD_ACTION'


@dataclass(frozen=True)
class InteractionTarget:
    """Retain native context and evidence locally, exposing only command capability."""

    id: str
    command: str
    pid: int
    bundle: str
    element: object = field(compare=False, repr=False)
    window: object = field(compare=False, repr=False)
    focused: object = field(compare=False, repr=False)
    editor: object = field(default=None, compare=False, repr=False)
    bar: object = field(default=None, compare=False, repr=False)
    value: float | None = None
    minimum: float = 0
    maximum: float = 1
    point: tuple | None = None
    clipboard_count: int | None = None
    menu_label: str = ''
    field_id: str | None = None
    area_label: str = ''
    tabs: tuple = field(default=(), compare=False, repr=False)
    pages: tuple = field(default=(), compare=False, repr=False)
    page_busy: bool = False
    reset_zoom_enabled: bool | None = None

    def provider_target(self):
        """Expose command capabilities without native handles or document values."""

        return {'scope': 'interaction', 'command': self.command, 'bundle_identifier': self.bundle,
                'process_identifier': self.pid, 'label': self.menu_label or self.area_label or self.command.replace('_', ' '),
                'field_id': self.field_id, 'requires_confirmation': self.command in {'cut', 'paste', 'close_tab'} or
                self.command == 'enter' and not (self.editor and self.editor.role == 'AXTextArea' and self.bundle in {'com.apple.TextEdit', 'com.apple.Notes'})}


class NativeInteractions:
    """Reuse observed editor/menu facts; never send arbitrary key combinations."""

    def __init__(self, text, *, pasteboard=None):
        self.text = text
        self.pasteboard = pasteboard if pasteboard is not None else NSPasteboard.generalPasteboard()
        self.previous = ()
        self.next_id = 0

    def clipboard_count(self):
        """Observe clipboard change identity without reading its contents."""

        return int(self.pasteboard.changeCount())

    def scan(self, state, application, window):
        """Collect enabled menu commands and scroll ranges within a bounded AX walk."""

        text, ax = self.text, self.text.ax
        text.scan_deadline = monotonic() + 0.5
        focused = text.read(application, 'AXFocusedUIElement')
        if focused is None or not text.safe_ancestry(focused):
            return ()
        editor = next((item for item in state.text_fields if item.focused and item.can_type), None)
        menus, areas, visited, tabs, pages = {}, [], [], [], []
        page_busy = False
        reset_zoom_enabled = None
        queue = deque([text.read(application, 'AXMenuBar'), window])
        deadline = monotonic() + 0.5
        text.scan_deadline = deadline
        while queue and len(visited) < 256 and monotonic() < deadline:
            node = queue.popleft()
            if node is None or any(text.equal(node, old) for old in visited):
                continue
            visited.append(node)
            if not text.safe_ancestry(node):
                continue
            role = text.read(node, 'AXRole')
            if role == 'AXWebArea':
                pages.append(node)
                page_busy = page_busy or native_true(text.read(node, 'AXBusy'))
            if role == 'AXTabGroup':
                for tab in list(text.read(node, 'AXChildren') or ())[:64]:
                    tabs.append((tab, native_true(text.read(tab, 'AXSelected')) or native_true(text.read(tab, 'AXValue'))))
            if role == 'AXMenuItem' and str(text.read(node, 'AXTitle') or '').casefold() == 'actual size':
                enabled = text.read(node, 'AXEnabled')
                if isinstance(enabled, (bool, int)):
                    reset_zoom_enabled = native_true(enabled)
            if role == 'AXMenuItem' and native_true(text.read(node, 'AXEnabled')):
                label = str(text.read(node, 'AXTitle') or '')[:240].casefold().strip()
                error, actions = ax.AXUIElementCopyActionNames(node, None)
                if error == 0 and 'AXPress' in (actions or ()):
                    for command, labels in MENU_LABELS.items():
                        if label in labels or command in {'undo', 'redo'} and label.startswith(command + ' '):
                            menus.setdefault(command, []).append((node, label))
            if role == 'AXScrollArea':
                enabled = text.read(node, 'AXEnabled')
                if enabled is None or native_true(enabled):
                    areas.append(node)
            if role not in {'AXTextArea', 'AXTextField', 'AXTable', 'AXList', 'AXOutline'}:
                children = text.read(node, 'AXChildren') or ()
                queue.extend(list(children)[:max(0, 256 - len(queue))])
        candidates = []

        def add(command, element, **kwargs):
            old = next((item for item in self.previous if item.pid == state.active.pid and item.command == command and text.equal(item.element, element)), None)
            identifier = old.id if old else f'interaction_{self.next_id}'
            if not old:
                self.next_id += 1
            candidates.append(InteractionTarget(identifier, command, state.active.pid, state.active.bundle_id,
                                                element, window, focused, editor=editor,
                                                field_id=editor.id if editor else None, tabs=tuple(tabs), pages=tuple(pages),
                                                page_busy=page_busy, reset_zoom_enabled=reset_zoom_enabled, **kwargs))

        terminal = state.active.bundle_id in {'com.apple.Terminal', 'com.googlecode.iterm2'}
        if editor and not terminal:
            if editor.value and text.settable(editor.element, 'AXSelectedTextRange'):
                add('select_all', editor.element)
            add('enter', editor.element)
        if not terminal:
            for command, items in menus.items():
                if len(items) != 1:
                    continue
                if command in TEXT_COMMANDS and not editor:
                    continue
                if command == 'select_all' and (not editor.value or any(item.command == 'select_all' for item in candidates)):
                    continue
                if command in {'copy', 'cut'} and (not editor.selection or not editor.selection[1]):
                    continue
                if command in BROWSER_COMMANDS:
                    from .web_navigation import BROWSER_BUNDLES
                    if state.active.bundle_id not in BROWSER_BUNDLES:
                        continue
                add(command, items[0][0], menu_label=items[0][1], clipboard_count=self.clipboard_count() if command in {'copy', 'cut', 'paste'} else None)
            # Tab and Escape require a readable accessible focus, never Terminal.
            for command in ('tab', 'shift_tab', 'escape'):
                add(command, focused)
        ancestor = focused
        for _ in range(32):
            containing = next((area for area in areas if text.equal(area, ancestor)), None)
            if containing is not None:
                areas = [containing]
                break
            ancestor = text.read(ancestor, 'AXParent')
            if ancestor is None:
                break
        for area in areas[:16]:
            position, size = self.geometry(area, 'AXPosition', ax.kAXValueCGPointType), self.geometry(area, 'AXSize', ax.kAXValueCGSizeType)
            if not position or not size or size[0] <= 0 or size[1] <= 0:
                continue
            point = (position[0] + size[0] / 2, position[1] + size[1] / 2)
            label = str(text.read(area, 'AXDescription') or text.read(area, 'AXTitle') or 'Scrollable area')[:240]
            for attribute, directions in [('AXVerticalScrollBar', ('up', 'down')), ('AXHorizontalScrollBar', ('left', 'right'))]:
                bar = text.read(area, attribute)
                if bar is None:
                    continue
                enabled = text.read(bar, 'AXEnabled')
                if enabled is not None and not native_true(enabled):
                    continue
                value, minimum, maximum = (text.read(bar, key) for key in ('AXValue', 'AXMinValue', 'AXMaxValue'))
                if not all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) for v in (value, minimum, maximum)) or not minimum <= value <= maximum or minimum == maximum:
                    continue
                for direction, available in zip(directions, (value > minimum, value < maximum)):
                    if available:
                        for suffix in ('', '_small', '_large'):
                            add('scroll_' + direction + suffix, area, bar=bar, value=value, minimum=minimum, maximum=maximum, point=point, area_label=label)
                        if direction in {'up', 'down'}:
                            if text.settable(bar, 'AXValue'):
                                add('scroll_top' if direction == 'up' else 'scroll_bottom', area, bar=bar, value=value, minimum=minimum, maximum=maximum, point=point, area_label=label)
        self.previous = tuple(candidates)
        return self.previous

    def geometry(self, node, attribute, kind):
        """Decode native CGPoint/CGSize without guessing a target position."""

        value = self.text.read(node, attribute)
        if value is None:
            return None
        try:
            valid, pair = self.text.ax.AXValueGetValue(value, kind, None)
            result = tuple(float(v) for v in pair) if valid else ()
            return result if len(result) == 2 and all(math.isfinite(v) for v in result) else None
        except (TypeError, ValueError):
            return None

    def same_target(self, original, current):
        """Bind commands to identical focus/window and exact editor/scroll baseline."""

        text = self.text
        return (original == current and all(text.equal(a, b) for a, b in
                [(original.element, current.element), (original.window, current.window), (original.focused, current.focused)])
                and self.same_tabs(original.tabs, current.tabs)
                and len(original.pages) == len(current.pages) and all(text.equal(a, b) for a, b in zip(original.pages, current.pages))
                and (original.bar is None and current.bar is None or original.bar is not None and current.bar is not None and text.equal(original.bar, current.bar))
                and (original.editor is None and current.editor is None or original.editor is not None and current.editor is not None and text.same_target(original.editor, current.editor, typing=True)))

    def same_tabs(self, original, current):
        """Compare native tab identities and selection without reading tab/page text."""

        return len(original) == len(current) and all(selected == other_selected and self.text.equal(tab, other)
               for (tab, selected), (other, other_selected) in zip(original, current))

    def execute(self, target, guard):
        """Dispatch one selection/menu command or paired key/wheel event, without retries."""

        if target.menu_label:
            if not guard():
                raise TextInputError('The foreground or focus changed before the command.')
            error = self.text.ax.AXUIElementPerformAction(target.element, 'AXPress')
            if error != 0:
                raise TextInputError('The enabled menu command failed; it was not retried.', effect_sent=True)
            return
        q, command = self.text.quartz, target.command
        if command == 'select_all':
            value = self.text.ax.AXValueCreate(self.text.ax.kAXValueCFRangeType, (0, utf16_length(target.editor.value)))
            if value is None or not guard():
                raise TextInputError('The native text selection is no longer available.')
            error = self.text.ax.AXUIElementSetAttributeValue(target.element, 'AXSelectedTextRange', value)
            if error != 0:
                raise TextInputError('The text selection setter failed; it was not retried.', effect_sent=True)
            return
        if command in {'scroll_top', 'scroll_bottom'}:
            if not guard():
                raise TextInputError('The foreground or focus changed before scrolling.')
            error = self.text.ax.AXUIElementSetAttributeValue(target.bar, 'AXValue', target.minimum if command == 'scroll_top' else target.maximum)
            if error != 0:
                raise TextInputError('The scroll position setter failed; it was not retried.', effect_sent=True)
            return
        if not q.CGPreflightPostEventAccess():
            raise TextInputError('macOS denied event-posting permission.')
        if command.startswith('scroll_'):
            # Pixel units keep larger gestures within the documented event semantics.
            amount = 600 if command.endswith('_large') else 60 if command.endswith('_small') else 200
            backwards = command.startswith(('scroll_up', 'scroll_left')) or command == 'scroll_top'
            amount *= 1 if backwards else -1
            horizontal = command.startswith(('scroll_left', 'scroll_right'))
            event = q.CGEventCreateScrollWheelEvent(None, q.kCGScrollEventUnitPixel, 2, 0 if horizontal else amount, amount if horizontal else 0)
            if event is None:
                raise TextInputError('macOS could not create a scroll event.')
            q.CGEventSetLocation(event, target.point)
            q.CGEventSetFlags(event, 0)
            if not guard():
                raise TextInputError('The foreground or focus changed before scrolling.')
            q.CGEventPostToPid(target.pid, event)
            return
        code, flags = {'enter': (36, 0), 'tab': (48, 0), 'shift_tab': (48, kCGEventFlagMaskShift), 'escape': (53, 0)}[command]
        events = [q.CGEventCreateKeyboardEvent(None, code, down) for down in (True, False)]
        if any(event is None for event in events):
            raise TextInputError('macOS could not create paired keyboard events.')
        if not guard():
            raise TextInputError('The foreground or focus changed before the key press.')
        for event in events:
            q.CGEventSetFlags(event, flags)
        try:
            q.CGEventPostToPid(target.pid, events[0])
        finally:
            q.CGEventPostToPid(target.pid, events[1])

    def verified(self, target, before, state):
        """Require a concrete native effect; delivery alone never proves completion."""

        if state.active != before.active:
            return False
        if target.command.startswith('scroll_'):
            application = self.text.ax.AXUIElementCreateApplication(target.pid)
            if not self.text.equal(self.text.read(application, 'AXFocusedWindow'), target.window):
                return False
            attribute = 'AXHorizontalScrollBar' if target.command.startswith(('scroll_left', 'scroll_right')) else 'AXVerticalScrollBar'
            current_bar = self.text.read(target.element, attribute)
            if current_bar is None or not self.text.equal(current_bar, target.bar):
                return False
            value = self.text.read(target.bar, 'AXValue')
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not target.minimum <= value <= target.maximum:
                return False
            if target.command == 'scroll_top':
                return value == target.minimum
            if target.command == 'scroll_bottom':
                return value == target.maximum
            return value < target.value if target.command.startswith(('scroll_up', 'scroll_left')) else value > target.value
        original = target.editor
        current = next((item for item in state.text_fields if original and item.id == original.id), None)
        same = current and self.text.same_target(original, current, typing=False) and current.focused
        if target.command == 'select_all':
            return bool(same and current.value == original.value and current.selection == (0, utf16_length(original.value)))
        if target.command == 'enter' and original.role == 'AXTextArea':
            newline = bool(same and current.value == expected_insertion(original.value, original.selection, '\n') and current.selection == (original.selection[0] + 1, 0))
            if newline or target.bundle in {'com.apple.TextEdit', 'com.apple.Notes'}:
                return newline
        if target.command in {'copy', 'cut'}:
            clipboard_changed = self.clipboard_count() != target.clipboard_count
            return clipboard_changed and (bool(same and current.value == original.value and current.selection == original.selection) if target.command == 'copy' else bool(same and current.value == expected_insertion(original.value, original.selection, '') and current.selection == (original.selection[0], 0)))
        if target.command in {'paste', 'undo', 'redo'}:
            return bool(same and current.value != original.value)
        if target.command == 'close_tab' and not state.focused_window_available:
            return True
        observed = next((item for item in state.interaction_targets if item.id == target.id), None)
        if observed:
            if target.command in {'new_tab', 'close_tab', 'reopen_tab', 'next_tab', 'previous_tab'} and not self.same_tabs(target.tabs, observed.tabs):
                return True
            if target.command == 'reload' and (not target.page_busy and observed.page_busy or len(target.pages) == len(observed.pages) == 1 and not self.text.equal(target.pages[0], observed.pages[0])):
                return True
            if target.command in {'zoom_in', 'zoom_out', 'reset_zoom'} and target.reset_zoom_enabled is not None and observed.reset_zoom_enabled is not None and target.reset_zoom_enabled != observed.reset_zoom_enabled:
                return True
        application = self.text.ax.AXUIElementCreateApplication(target.pid)
        window, focus = self.text.read(application, 'AXFocusedWindow'), self.text.read(application, 'AXFocusedUIElement')
        return bool(window is not None and (not self.text.equal(window, target.window) or focus is not None and not self.text.equal(focus, target.focused))
                    or state.browser_url and state.browser_url != before.browser_url
                    or state.document_url and state.document_url != before.document_url)
