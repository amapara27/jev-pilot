"""Ground explicit keyboard commands and scrollbar positions in current native context."""

from collections import deque
from dataclasses import dataclass, field
import math
import re
from time import monotonic

from AppKit import NSPasteboard, NSPasteboardTypeString
from Quartz import kCGEventFlagMaskShift

from .text_input import MAX_VALUE_CHARACTERS, TextInputError, expected_insertion, native_true, utf16_length


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
WINDOW_COMMANDS = {'minimize': 'AXMinimized', 'maximize': 'AXFullScreen'}
MENU_BRANCHES = {'file': {'new_tab', 'close_tab', 'address_bar'},
                 'edit': TEXT_COMMANDS | {'find'},
                 'view': {'zoom_in', 'zoom_out', 'reset_zoom', 'reload'},
                 'history': {'back', 'forward', 'reopen_tab'},
                 'window': {'next_tab', 'previous_tab'}}
PATTERNS = [
    ('minimize', r'minimi[zs]e'), ('maximize', r'(?:maximi[zs]e|enter\s+full\s*screen)'),
    ('enter', r'(?:press|hit)\s+(?:the\s+)?(?:enter|return)(?:\s+key)?'),
    ('select_all', r'select\s+all(?:\s+text)?'),
    ('reopen_tab', r'reopen\s+(?:the\s+)?(?:last\s+)?(?:closed\s+)?tab'),
    ('close_tab', r'close\s+(?:(?:this|the|current)\s+)?tab'),
    ('new_tab', r'(?:open\s+)?(?:a\s+)?new\s+tab'),
    ('next_tab', r'(?:(?:go|switch)\s+to\s+)?(?:the\s+)?next\s+tab'),
    ('previous_tab', r'(?:(?:go\s+back|go|switch)\s+to\s+)?(?:the\s+)?previous\s+tab'),
    ('select_tab', r'(?:switch\s+(?:to|the)|(?:click|select)(?:\s+on)?)\s+(?:the\s+)?(?!next\b|previous\b).+?\s+tab'),
    ('back', r'go\s+back(?!\s+to\s+(?:the\s+)?(?:previous|next)\s+tab)'), ('forward', r'go\s+forward'),
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
        boundary = re.search(r'\s+(?:and(?:\s+then)?|then)\s+(?=(?:press|hit|select all|scroll|go to the|copy\b|cut\b|paste\b|undo\b|redo\b|close\b|reload\b|zoom\b|minimi[zs]e\b|maximi[zs]e\b|enter\s+full\s*screen\b))', tail, re.I) if literal.group().strip().casefold() in {'type', 'write', 'dictate', 'insert'} else None
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
    extent: float | None = None  # scrollable pixels along the bar's axis (content minus viewport)
    clipboard_count: int | None = None
    menu_label: str = ''
    field_id: str | None = None
    area_label: str = ''
    tabs: tuple = field(default=(), compare=False, repr=False)
    pages: tuple = field(default=(), compare=False, repr=False)
    page_busy: bool = field(default=False, compare=False)
    reset_zoom_enabled: bool | None = field(default=None, compare=False)
    focus_sensitive: bool = True
    clipboard_text: str | None = field(default=None, compare=False, repr=False)
    zoom: float | None = field(default=None, compare=False)
    samples: tuple = field(default=(), compare=False, repr=False)
    window_attribute: str = ''
    window_value: bool | None = None

    def provider_target(self):
        """Expose command capabilities without native handles or document values."""

        return {'scope': 'interaction', 'command': self.command, 'bundle_identifier': self.bundle,
                'process_identifier': self.pid, 'label': self.menu_label or self.area_label or self.command.replace('_', ' '),
                'field_id': self.field_id, 'requires_confirmation': self.command in {'cut', 'close_tab'} or
                self.command == 'enter' and not (self.editor and self.editor.role == 'AXTextArea' and self.bundle in {'com.apple.TextEdit', 'com.apple.Notes'})}


class NativeInteractions:
    """Reuse observed editor/menu facts; never send arbitrary key combinations."""

    def __init__(self, text, *, pasteboard=None):
        self.text = text
        self.pasteboard = pasteboard if pasteboard is not None else NSPasteboard.generalPasteboard()
        self.previous = ()
        self.next_id = 0
        self.diagnostics = {}

    def clipboard_count(self):
        """Observe clipboard change identity without reading its contents."""

        return int(self.pasteboard.changeCount())

    def scan(self, state, application, window, commands=()):
        """Collect enabled menu commands and scroll ranges within a bounded AX walk."""

        text, ax = self.text, self.text.ax
        text.scan_deadline = monotonic() + 0.5
        focus_error, focused = ax.AXUIElementCopyAttributeValue(application, 'AXFocusedUIElement', None)
        focus_safe = focused is not None and text.safe_ancestry(focused)
        self.diagnostics = {'focused_element_ax_error': int(focus_error), 'focused_element_available': focused is not None,
                            'focused_element_safe': focus_safe}
        if not text.safe_ancestry(window):
            self.diagnostics['window_safe'] = False
            return ()
        self.diagnostics['window_safe'] = True
        if not focus_safe:
            focused = None
        editor = next((item for item in state.text_fields if focus_safe and item.focused and item.can_type and text.equal(item.element, focused)), None)
        menus, areas, visited, tabs, pages = {}, [], [], [], []
        page_busy = False
        reset_zoom_enabled = None
        zoom, samples = None, []
        branches = {branch for branch, names in MENU_BRANCHES.items() if not commands or set(commands) & names}
        menu_error, menu = ax.AXUIElementCopyAttributeValue(application, 'AXMenuBar', None)
        self.diagnostics['menu_bar_ax_error'] = int(menu_error)
        # Page widgets can expose the same tab/zoom roles as browser controls.
        queue = deque((node, False) for node in ([menu, window] if branches else [window]))
        deferred = deque()
        deadline = monotonic() + 0.5
        text.scan_deadline = deadline
        while (queue or deferred) and len(visited) < 256 and monotonic() < deadline:
            node, in_webpage = (queue or deferred).popleft()
            if node is None or any(text.equal(node, old) for old in visited):
                continue
            visited.append(node)
            if not text.safe_ancestry(node):
                continue
            role = text.read(node, 'AXRole')
            if role == 'AXWebArea':
                pages.append(node)
                page_busy = page_busy or native_true(text.read(node, 'AXBusy'))
            if role == 'AXTabGroup' and not in_webpage:
                for tab in list(text.read(node, 'AXTabs') or text.read(node, 'AXChildren') or ())[:64]:
                    if text.read(tab, 'AXRole') in {'AXRadioButton', 'AXTab'} or text.read(tab, 'AXSubrole') == 'AXTabButton':
                        tabs.append((tab, native_true(text.read(tab, 'AXSelected')) or native_true(text.read(tab, 'AXValue'))))
            # Newer Safari lists its tab buttons in a tab-bar group beside an empty AXTabGroup.
            if (not in_webpage and role in {'AXRadioButton', 'AXTab'} and text.read(node, 'AXSubrole') == 'AXTabButton'
                    and len(tabs) < 64 and not any(text.equal(node, tab) for tab, _ in tabs)):
                tabs.append((node, native_true(text.read(node, 'AXSelected')) or native_true(text.read(node, 'AXValue'))))
            label = str(text.read(node, 'AXTitle') or text.read(node, 'AXDescription') or '')[:240].casefold().strip()
            if role == 'AXMenuBarItem' and label not in branches:
                continue
            if not in_webpage and role in {'AXButton', 'AXPopUpButton', 'AXTextField'} and 'zoom' in label:
                match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*%\s*', str(text.read(node, 'AXValue') or text.read(node, 'AXValueDescription') or ''))
                if match:
                    zoom = float(match.group(1))
            if role in {'AXHeading', 'AXStaticText', 'AXImage'} and pages and len(samples) < 8:
                position = self.geometry(node, 'AXPosition', getattr(ax, 'kAXValueCGPointType', 1))
                size = self.geometry(node, 'AXSize', getattr(ax, 'kAXValueCGSizeType', 2))
                if position and size:
                    samples.append((node, position, size))
            if role == 'AXMenuItem' and str(text.read(node, 'AXTitle') or '').casefold() == 'actual size':
                enabled = text.read(node, 'AXEnabled')
                if isinstance(enabled, (bool, int)):
                    reset_zoom_enabled = native_true(enabled)
            enabled_error, enabled = ax.AXUIElementCopyAttributeValue(node, 'AXEnabled', None) if role == 'AXMenuItem' else (0, False)
            if role == 'AXMenuItem' and (enabled_error == -25205 or enabled_error == 0 and native_true(enabled)):
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
                pending = [(child, in_webpage or role == 'AXWebArea')
                           for child in list(children)[:max(0, 256 - len(queue) - len(deferred))]]
                if role == 'AXWebArea':
                    deferred.extend(pending)
                elif not commands or set(commands) - WINDOW_COMMANDS.keys():
                    queue.extendleft(reversed(pending))
            if len(visited) == 128 and not any(text.equal(window, old) for old in visited):
                queue.appendleft((window, False))
        candidates = []
        self.diagnostics.update(visited_nodes=len(visited), scan_limit_reached=bool(queue or deferred),
                                menu_command_count=len(menus), scroll_area_count=len(areas), tab_count=len(tabs))

        def add(command, element, **kwargs):
            old = next((item for item in self.previous if item.pid == state.active.pid and item.command == command and text.equal(item.element, element)), None)
            identifier = old.id if old else f'interaction_{self.next_id}'
            if not old:
                self.next_id += 1
            sensitive = command in TEXT_COMMANDS | {'tab', 'shift_tab', 'escape'}
            bound_editor = editor if command in TEXT_COMMANDS else None
            candidates.append(InteractionTarget(identifier, command, state.active.pid, state.active.bundle_id,
                                                element, window, focused, editor=bound_editor,
                                                field_id=bound_editor.id if bound_editor else None, tabs=tuple(tabs), pages=tuple(pages),
                                                page_busy=page_busy, reset_zoom_enabled=reset_zoom_enabled, focus_sensitive=sensitive,
                                                zoom=zoom, samples=tuple(samples), **kwargs))

        terminal = state.active.bundle_id in {'com.apple.Terminal', 'com.googlecode.iterm2'}
        self.diagnostics['window_control_checks'] = []
        for command, attribute in WINDOW_COMMANDS.items():
            error, value = ax.AXUIElementCopyAttributeValue(window, attribute, None)
            readable = error == 0 and isinstance(value, (bool, int)) and value in (0, 1)
            writable = text.settable(window, attribute)
            self.diagnostics['window_control_checks'].append({'command': command, 'attribute_ax_error': int(error),
                                                              'readable_boolean': readable, 'writable': writable})
            if readable and writable:
                add(command, window, window_attribute=attribute, window_value=bool(value))
        if editor and not terminal:
            if editor.value and text.settable(editor.element, 'AXSelectedTextRange'):
                add('select_all', editor.element)
            add('enter', editor.element)
        if not terminal:
            for command, items in menus.items():
                if commands and command not in commands:
                    continue
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
                clipboard_text = None
                clipboard_count = self.clipboard_count() if command in {'copy', 'cut', 'paste'} else None
                if command == 'paste':
                    value = self.pasteboard.stringForType_(NSPasteboardTypeString)
                    if self.clipboard_count() != clipboard_count or not isinstance(value, str) or not value or len(value) > MAX_VALUE_CHARACTERS:
                        continue
                    if len(expected_insertion(editor.value, editor.selection, value)) > MAX_VALUE_CHARACTERS:
                        continue
                    clipboard_text = value
                add(command, items[0][0], menu_label=items[0][1], clipboard_count=clipboard_count,
                    clipboard_text=clipboard_text)
            if state.active.bundle_id == 'com.apple.Safari':
                for tab, selected in tabs:
                    error, actions = ax.AXUIElementCopyActionNames(tab, None)
                    if not selected and error == 0 and 'AXPress' in (actions or ()):
                        add('select_tab', tab, menu_label=str(text.read(tab, 'AXTitle') or text.read(tab, 'AXDescription') or 'Browser tab')[:240])
            # Tab and Escape require a readable accessible focus, never Terminal.
            if focus_safe:
                for command in ('tab', 'shift_tab', 'escape'):
                    add(command, focused)
        ancestor = focused
        for _ in range(32):
            if ancestor is None:
                break
            containing = next((area for area in areas if text.equal(area, ancestor)), None)
            if containing is not None:
                areas = [containing]
                break
            ancestor = text.read(ancestor, 'AXParent')
            if ancestor is None:
                break
        self.diagnostics['scrollbar_checks'] = []
        for area in areas[:16]:
            position, size = self.geometry(area, 'AXPosition', ax.kAXValueCGPointType), self.geometry(area, 'AXSize', ax.kAXValueCGSizeType)
            if not position or not size or size[0] <= 0 or size[1] <= 0:
                self.diagnostics['scroll_geometry_unavailable'] = self.diagnostics.get('scroll_geometry_unavailable', 0) + 1
                continue
            label = str(text.read(area, 'AXDescription') or text.read(area, 'AXTitle') or 'Scrollable area')[:240]
            contents = list(text.read(area, 'AXContents') or ())
            content = self.geometry(contents[0], 'AXSize', ax.kAXValueCGSizeType) if len(contents) == 1 else None
            # axis indexes (width, height): a vertical bar scrolls through content height.
            for axis, attribute, directions in [(1, 'AXVerticalScrollBar', ('up', 'down')), (0, 'AXHorizontalScrollBar', ('left', 'right'))]:
                bar = self.scrollbar(area, attribute)
                check = {'axis': directions[0], 'bar_present': bar is not None}
                self.diagnostics['scrollbar_checks'].append(check)
                if bar is None:
                    continue
                enabled = text.read(bar, 'AXEnabled')
                if enabled is not None and not native_true(enabled):
                    continue
                reads = [ax.AXUIElementCopyAttributeValue(bar, key, None) for key in ('AXValue', 'AXMinValue', 'AXMaxValue')]
                value, minimum, maximum = (item if error == 0 else None for error, item in reads)
                # WebKit reports min/max as unsupported or, on newer macOS, as 0/0 beside a 0–1 value.
                missing_range = (all(error == -25205 for error, _ in reads[1:])
                                 or all(error == 0 and item == 0 and not isinstance(item, bool) for error, item in reads[1:]))
                normalized = missing_range and state.active.bundle_id in {'com.apple.Safari', 'com.apple.finder'} and text.read(bar, 'AXRole') == 'AXScrollBar'
                if normalized:
                    # Standard WebKit/AppKit scrollers expose a normalized position without min/max.
                    minimum, maximum = 0, 1
                valid = all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) for v in (value, minimum, maximum)) and minimum <= value <= maximum and minimum != maximum
                writable = text.settable(bar, 'AXValue')
                # A pixel step needs the measured scrollable distance; without it only top/bottom remain.
                extent = content[axis] - size[axis] if content else None
                extent = extent if extent and extent > 0 else None
                check.update(value_ax_error=int(reads[0][0]), min_ax_error=int(reads[1][0]), max_ax_error=int(reads[2][0]),
                             value_type=type(value).__name__[:64], range_source='normalized' if normalized else 'native',
                             valid_range=bool(valid), position_writable=writable, content_extent_available=extent is not None)
                if not valid or not writable:
                    continue
                for direction, available in zip(directions, (value > minimum, value < maximum)):
                    if available:
                        if extent is not None:
                            for suffix in ('', '_small', '_large'):
                                add('scroll_' + direction + suffix, area, bar=bar, value=value, minimum=minimum, maximum=maximum, extent=extent, area_label=label)
                        if direction in {'up', 'down'}:
                            add('scroll_top' if direction == 'up' else 'scroll_bottom', area, bar=bar, value=value, minimum=minimum, maximum=maximum, extent=extent, area_label=label)
        self.previous = tuple(candidates)
        return self.previous

    def scrollbar(self, area, attribute):
        """Accept one native axis scrollbar, including child-only WebKit exposure."""

        bar = self.text.read(area, attribute)
        if bar is not None:
            return bar
        orientation = 'AXHorizontalOrientation' if attribute == 'AXHorizontalScrollBar' else 'AXVerticalOrientation'
        bars = [node for node in list(self.text.read(area, 'AXChildren') or ())[:64]
                if self.text.read(node, 'AXRole') == 'AXScrollBar' and self.text.read(node, 'AXOrientation') == orientation]
        return bars[0] if len(bars) == 1 else None

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
                [(original.element, current.element), (original.window, current.window)])
                and (not original.focus_sensitive or text.equal(original.focused, current.focused))
                and (original.command in TEXT_COMMANDS or original.command in WINDOW_COMMANDS or self.same_tabs(original.tabs, current.tabs))
                and (original.bar is None and current.bar is None or original.bar is not None and current.bar is not None and text.equal(original.bar, current.bar))
                and (original.editor is None and current.editor is None or original.editor is not None and current.editor is not None and text.same_target(original.editor, current.editor, typing=True)))

    def same_tabs(self, original, current):
        """Compare native tab identities and selection without reading tab/page text."""

        return len(original) == len(current) and all(selected == other_selected and self.text.equal(tab, other)
               for (tab, selected), (other, other_selected) in zip(original, current))

    def execute(self, target, guard):
        """Dispatch one selection/menu command or paired key/wheel event, without retries."""

        if target.window_attribute:
            if not guard():
                raise TextInputError('The foreground window changed before the window action.')
            error = self.text.ax.AXUIElementSetAttributeValue(target.window, target.window_attribute, True)
            if error:
                raise TextInputError('The native window action failed; it was not retried.', effect_sent=True)
            return
        if target.menu_label:
            if not guard():
                raise TextInputError('The foreground or focus changed before the command.')
            if target.command == 'paste' and self.clipboard_count() != target.clipboard_count:
                raise TextInputError('The clipboard changed before paste; no effect was sent.')
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
        if command.startswith('scroll_'):
            if command in {'scroll_top', 'scroll_bottom'}:
                position = target.minimum if command == 'scroll_top' else target.maximum
            else:
                # PID-posted wheel events no longer reach AppKit/WebKit views, and routed ones move the
                # user's pointer, so convert the pixel step through measured geometry into a bar position.
                pixels = 600 if command.endswith('_large') else 60 if command.endswith('_small') else 200
                step = pixels / target.extent * (target.maximum - target.minimum)
                backwards = command.startswith(('scroll_up', 'scroll_left'))
                position = min(target.maximum, max(target.minimum, target.value - step if backwards else target.value + step))
            if not guard():
                raise TextInputError('The foreground or focus changed before scrolling.')
            error = self.text.ax.AXUIElementSetAttributeValue(target.bar, 'AXValue', position)
            if error != 0:
                raise TextInputError('The scroll position setter failed; it was not retried.', effect_sent=True)
            return
        if not q.CGPreflightPostEventAccess():
            raise TextInputError('macOS denied event-posting permission.')
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

        if not state.ax_trusted:
            return False
        if target.window_attribute:
            application = self.text.application(target.pid)
            windows = self.text.read(application, 'AXWindows')
            present = windows is not None and any(self.text.equal(window, target.window) for window in windows)
            return bool(present and native_true(self.text.read(target.window, target.window_attribute))
                        and (target.command == 'minimize' or state.active == before.active))
        if state.active != before.active:
            return False
        if target.command.startswith('scroll_'):
            application = self.text.application(target.pid)
            if not self.text.equal(self.text.read(application, 'AXFocusedWindow'), target.window):
                return False
            attribute = 'AXHorizontalScrollBar' if target.command.startswith(('scroll_left', 'scroll_right')) else 'AXVerticalScrollBar'
            current_bar = self.scrollbar(target.element, attribute)
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
        if target.command == 'paste':
            return bool(same and current.value == expected_insertion(original.value, original.selection, target.clipboard_text)
                        and current.selection == (original.selection[0] + utf16_length(target.clipboard_text), 0))
        if target.command in {'undo', 'redo'}:
            return bool(same and current.value != original.value)
        if target.command == 'close_tab':
            application = self.text.application(target.pid)
            windows = self.text.read(application, 'AXWindows')
            if windows is not None and all(not self.text.equal(window, target.window) for window in windows):
                return True
        observed = next((item for item in state.interaction_targets if item.id == target.id), None)
        if observed is None and target.command in BROWSER_COMMANDS | {'select_tab'}:
            observed = next((item for item in state.interaction_targets if item.pid == target.pid and self.text.equal(item.window, target.window)), None)
        if observed:
            if target.command in BROWSER_COMMANDS | {'select_tab'} and not self.text.equal(target.window, observed.window):
                return False
            if target.command in {'new_tab', 'close_tab', 'reopen_tab'}:
                if target.command == 'close_tab' and len(observed.tabs) < len(target.tabs):
                    return True
                if target.command != 'close_tab' and len(observed.tabs) > len(target.tabs):
                    return True
            if target.command in {'next_tab', 'previous_tab'}:
                if len(target.tabs) < 2 or len(target.tabs) != len(observed.tabs) or not all(
                        self.text.equal(tab, current) for (tab, _), (current, _) in zip(target.tabs, observed.tabs)):
                    return False
                previous = [index for index, (_, selected) in enumerate(target.tabs) if selected]
                current = [index for index, (_, selected) in enumerate(observed.tabs) if selected]
                return len(previous) == 1 and current == [(previous[0] + (1 if target.command == 'next_tab' else -1)) % len(target.tabs)]
            if target.command == 'select_tab' and any(self.text.equal(tab, target.element) and selected for tab, selected in observed.tabs):
                return True
            if target.command == 'reload' and (not target.page_busy and observed.page_busy or len(target.pages) == len(observed.pages) == 1 and not self.text.equal(target.pages[0], observed.pages[0])):
                return True
            if target.command in {'back', 'forward'}:
                return bool(state.browser_url and state.browser_url != before.browser_url)
            if target.command in {'zoom_in', 'zoom_out', 'reset_zoom'}:
                if before.browser_url != state.browser_url or not self.same_tabs(target.tabs, observed.tabs):
                    return False
                if target.zoom is not None and observed.zoom is not None:
                    return observed.zoom > target.zoom if target.command == 'zoom_in' else observed.zoom < target.zoom if target.command == 'zoom_out' else observed.zoom == 100
                if target.command == 'reset_zoom' and target.reset_zoom_enabled is True and observed.reset_zoom_enabled is False:
                    return True
                for old, _, size in target.samples:
                    current_sample = next((sample for sample in observed.samples if self.text.equal(old, sample[0])), None)
                    if current_sample and all(value > 0 for value in (*size, *current_sample[2])):
                        ratios = [new / previous for new, previous in zip(current_sample[2], size)]
                        if math.isclose(*ratios, rel_tol=0.05) and (min(ratios) > 1 if target.command == 'zoom_in' else max(ratios) < 1 if target.command == 'zoom_out' else False):
                            return True
        application = self.text.application(target.pid)
        window, focus = self.text.read(application, 'AXFocusedWindow'), self.text.read(application, 'AXFocusedUIElement')
        if target.command in BROWSER_COMMANDS - {'address_bar'} | {'select_tab'}:
            return False
        return bool(window is not None and (not self.text.equal(window, target.window) or focus is not None and not self.text.equal(focus, target.focused))
                    or state.browser_url and state.browser_url != before.browser_url
                    or state.document_url and state.document_url != before.document_url)
