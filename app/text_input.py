"""Ground native editors and insert exact text without clipboard or submission keys."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable
import unicodedata
from time import monotonic

import ApplicationServices as AX
from CoreFoundation import CFEqual
import Quartz


MAX_VALUE_CHARACTERS = 65_536
MAX_NODES = 256
MAX_FIELDS = 32
TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox"}
COLLECTION_ROLES = {"AXTable", "AXOutline", "AXList", "AXBrowser", "AXCollection"}


def native_true(value: object) -> bool:
    """Accept native Boolean true without requiring Python singleton identity."""

    return isinstance(value, (bool, int)) and value == 1


class TextInputError(RuntimeError):
    """Display a local typing failure without exposing document contents."""

    def __init__(self, message: str, *, effect_sent: bool = False):
        super().__init__(message)
        self.effect_sent = effect_sent


def utf16_length(text: str) -> int:
    """Use native UTF-16 units rather than Python code points for caret arithmetic."""

    return len(text.encode("utf-16-le")) // 2


def expected_insertion(value: str, selection: tuple[int, int], text: str) -> str:
    """Replace only a valid UTF-16 selection, rejecting split surrogate pairs."""

    start, length = selection
    units = value.encode("utf-16-le")
    if start < 0 or length < 0 or (start + length) * 2 > len(units):
        raise TextInputError("The editable field has an invalid caret or selection.")
    try:
        prefix = units[:start * 2].decode("utf-16-le")
        suffix = units[(start + length) * 2:].decode("utf-16-le")
    except UnicodeError as error:
        raise TextInputError("The selection splits a Unicode character.") from error
    return prefix + text + suffix


def unicode_chunks(text: str) -> list[tuple[int, ...]]:
    """Keep surrogate pairs together in bounded paired keyboard events."""

    chunks, current = [], []
    for character in text:
        raw = character.encode("utf-16-le")
        units = [int.from_bytes(raw[index:index + 2], "little") for index in range(0, len(raw), 2)]
        if len(current) + len(units) > 20:
            chunks.append(tuple(current))
            current = []
        current.extend(units)
    if current:
        chunks.append(tuple(current))
    return chunks


@dataclass(frozen=True)
class TextField:
    """Keep document text and AX handles local; expose only bounded targeting facts."""

    id: str
    pid: int
    bundle_id: str
    role: str
    label: str
    focused: bool
    focusable: bool
    selected_text_writable: bool
    keyboard_writable: bool
    value: str | None
    selection: tuple[int, int] | None
    element: object = field(compare=False, repr=False)
    window: object = field(compare=False, repr=False)

    @property
    def can_type(self) -> bool:
        """Only offer insertion when an exact baseline can be verified locally."""

        if not self.focused or self.value is None or self.selection is None:
            return False
        try:
            expected_insertion(self.value, self.selection, "")
        except (TextInputError, UnicodeError):
            return False
        if self.bundle_id == "com.apple.Terminal":
            # Never mistake a selection in Terminal's output for the input caret.
            return self.role == "AXTextArea" and self.selection == (utf16_length(self.value), 0)
        return self.selected_text_writable or self.keyboard_writable

    def provider_target(self) -> dict[str, object]:
        """Withhold document values, caret text, and native object handles from Jev."""

        return {
            "scope": "field", "element_id": self.id, "bundle_identifier": self.bundle_id,
            "process_identifier": self.pid, "role": self.role, "label": self.label,
            "focused": self.focused, "focusable": self.focusable, "can_type": self.can_type,
        }


class NativeText:
    """Read bounded AX fields and perform one guarded insertion or focus effect."""

    def __init__(self, *, ax=AX, quartz=Quartz, equal=CFEqual):
        self.ax, self.quartz, self.equal = ax, quartz, equal
        self.previous: tuple[TextField, ...] = ()
        self.next_id = 0
        self.scan_deadline = float("inf")
        self.diagnostics: dict[str, object] = {}

    def count_reason(self, group: str, reason: str) -> None:
        """Count discovery failures without retaining labels or document contents."""

        counts = self.diagnostics.setdefault(group, {})
        counts[reason] = counts.get(reason, 0) + 1

    def read(self, element: object, name: str) -> object | None:
        """Treat unsupported/unreadable AX attributes as absent, not guessed values."""

        error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if error == 0 else None

    def settable(self, element: object, name: str) -> bool:
        """Check native write support before offering a focus or text operation."""

        error, writable = self.ax.AXUIElementIsAttributeSettable(element, name, None)
        return error == 0 and bool(writable)

    def safe_ancestry(self, element: object) -> bool:
        """Reject protected fields and descendants before ever reading their values."""

        current = element
        for _ in range(64):
            if monotonic() >= self.scan_deadline:
                return False
            role_error, raw_role = self.ax.AXUIElementCopyAttributeValue(current, "AXRole", None)
            subrole_error, raw_subrole = self.ax.AXUIElementCopyAttributeValue(current, "AXSubrole", None)
            absent = {AX.kAXErrorAttributeUnsupported, AX.kAXErrorNoValue}
            if role_error != 0 or subrole_error not in absent | {0}:
                self.count_reason("ancestry_rejections", f"role_error_{role_error}_subrole_error_{subrole_error}")
                return False
            role = str(raw_role or "").casefold()
            subrole = str(raw_subrole or "").casefold()
            if "secure" in role + subrole or "password" in role + subrole:
                self.count_reason("ancestry_rejections", "secure_role")
                return False
            protected_error, protected = self.ax.AXUIElementCopyAttributeValue(current, "AXProtectedContent", None)
            if protected_error not in absent | {0}:
                self.count_reason("ancestry_rejections", f"protected_error_{protected_error}")
                return False
            if protected is not None and bool(protected):
                self.count_reason("ancestry_rejections", "protected_content")
                return False
            parent_error, parent = self.ax.AXUIElementCopyAttributeValue(current, "AXParent", None)
            if parent_error not in absent | {0}:
                self.count_reason("ancestry_rejections", f"parent_error_{parent_error}")
                return False
            if parent is None:
                return True
            current = parent
        return False

    def inspect(self, element: object, pid: int, bundle_id: str, window: object, focused: object) -> TextField | None:
        """Retain focused text only after native availability and write checks."""

        role = self.read(element, "AXRole")
        if role not in TEXT_ROLES:
            return None
        is_focused = focused is not None and bool(self.equal(element, focused))
        enabled_error, enabled = self.ax.AXUIElementCopyAttributeValue(element, "AXEnabled", None)
        check = {"role": role, "focused": is_focused, "enabled_ax_error": int(enabled_error),
                 "enabled_value_type": type(enabled).__name__, "enabled_true": native_true(enabled)}
        checks = self.diagnostics.setdefault("text_field_checks", [])
        if len(checks) < MAX_FIELDS:
            checks.append(check)
        enabled_unsupported = enabled_error == AX.kAXErrorAttributeUnsupported
        if (enabled_error != 0 and not enabled_unsupported) or (enabled_error == 0 and not native_true(enabled)):
            reason = f"enabled_error_{enabled_error}" if enabled_error else "disabled_or_invalid_enabled"
            self.count_reason("excluded_text_fields", reason)
            return None
        if not self.safe_ancestry(element):
            return None
        observed_window = self.read(element, "AXWindow")
        if observed_window is not None and not self.equal(observed_window, window):
            self.count_reason("excluded_text_fields", "different_window")
            return None
        writable = self.settable(element, "AXSelectedText")
        keyboard = self.settable(element, "AXValue") or native_true(self.read(element, "AXEditable"))
        focusable = self.settable(element, "AXFocused")
        check.update(focusable=focusable, selected_text_writable=writable, keyboard_writable=keyboard)
        terminal = bundle_id == "com.apple.Terminal" and role == "AXTextArea"
        # Notes omits AXEnabled on its body. Only that specific unsupported
        # attribute may defer to positive write support; false/errors still block.
        # Terminal's special candidate allowance is not positive write evidence.
        if not (writable or keyboard or (terminal and not enabled_unsupported)):
            self.count_reason("excluded_text_fields", "no_write_support")
            return None
        check["availability_check"] = "write_support" if enabled_unsupported else "enabled_attribute"
        value, selection = None, None
        if is_focused:
            observed = self.read(element, "AXValue")
            if isinstance(observed, str) and len(observed) <= MAX_VALUE_CHARACTERS:
                value = observed
            raw_range = self.read(element, "AXSelectedTextRange")
            if raw_range is not None:
                try:
                    success, native_range = self.ax.AXValueGetValue(raw_range, self.ax.kAXValueCFRangeType, None)
                    if success:
                        selection = (int(native_range[0]), int(native_range[1]))
                except (TypeError, ValueError):
                    pass
        if not is_focused:
            self.count_reason("unready_text_fields", "not_focused")
        elif value is None:
            self.count_reason("unready_text_fields", "unreadable_or_oversized_value")
        elif selection is None:
            self.count_reason("unready_text_fields", "missing_selection")
        else:
            try:
                expected_insertion(value, selection, "")
            except (TextInputError, UnicodeError):
                self.count_reason("unready_text_fields", "invalid_selection")
        old = next((item for item in self.previous if item.pid == pid and self.equal(item.element, element)), None)
        if old:
            identifier = old.id
        else:
            identifier = f"field_{self.next_id}"
            self.next_id += 1
        labels = [self.read(element, name) for name in ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXIdentifier")]
        label = next((item for item in labels if isinstance(item, str) and item), role)[:240]
        return TextField(identifier, pid, bundle_id, role, label, is_focused,
                         focusable, writable, keyboard or terminal,
                         value, selection, element, window)

    def scan(self, pid: int, bundle_id: str, application: object, window: object) -> tuple[TextField, ...]:
        """Reach editor panes before broad lists consume the bounded scan budget."""

        self.scan_deadline = monotonic() + 2
        self.diagnostics = {"excluded_text_fields": {}, "unready_text_fields": {},
                            "protected_or_unreadable_nodes": 0, "deferred_collections": 0,
                            "queued_nodes_dropped": 0}
        focused = self.read(application, "AXFocusedUIElement")
        queue, deferred = deque([window]), deque()
        # Inspect deep focused editors directly, but a focused sidebar row/table
        # must not bring its entire subtree ahead of the actual document pane.
        if focused is not None and self.read(focused, "AXRole") in TEXT_ROLES:
            queue.appendleft(focused)
        visited, fields = [], []
        while (queue or deferred) and len(visited) < MAX_NODES and len(fields) < MAX_FIELDS and monotonic() < self.scan_deadline:
            element = (queue or deferred).popleft()
            if element is None or any(self.equal(element, old) for old in visited):
                continue
            visited.append(element)
            if not self.safe_ancestry(element):
                self.diagnostics["protected_or_unreadable_nodes"] += 1
                continue
            item = self.inspect(element, pid, bundle_id, window, focused)
            if item:
                fields.append(item)
            role = self.read(element, "AXRole")
            # A text area's inline content is not another editor pane. Avoid
            # spending the discovery budget on every paragraph or attachment.
            if role in TEXT_ROLES:
                continue
            children = None
            if role in COLLECTION_ROLES or role == "AXScrollArea":
                children = self.read(element, "AXVisibleChildren")
            if children is None:
                children = self.read(element, "AXChildren")
            if children is not None and not isinstance(children, str):
                destination = deferred if role in COLLECTION_ROLES else queue
                capacity = MAX_NODES - len(destination)
                pending = children[:capacity]
                self.diagnostics["queued_nodes_dropped"] += max(0, len(children) - capacity)
                if role in COLLECTION_ROLES:
                    self.diagnostics["deferred_collections"] += 1
                    deferred.extend(pending)
                else:
                    # Depth-first container traversal reaches nested editors,
                    # while collection children remain behind sibling panes.
                    queue.extendleft(reversed(pending))
        self.diagnostics.update(visited_nodes=len(visited),
                                scan_limit_reached=bool(queue or deferred or self.diagnostics["queued_nodes_dropped"]))
        self.previous = tuple(fields)
        return self.previous

    def same_target(self, before: TextField, after: TextField, *, typing: bool) -> bool:
        """Match native identity plus effect-relevant facts, including exact text/caret."""

        stable = (before.id, before.pid, before.bundle_id, before.role, before.label,
                  before.focusable, before.selected_text_writable, before.keyboard_writable)
        fresh = (after.id, after.pid, after.bundle_id, after.role, after.label,
                 after.focusable, after.selected_text_writable, after.keyboard_writable)
        same = stable == fresh and self.equal(before.element, after.element) and self.equal(before.window, after.window)
        if typing:
            same = same and after.can_type and before.focused == after.focused
            same = same and before.value == after.value and before.selection == after.selection
        return bool(same)

    def focus(self, target: TextField, guard: Callable[[], bool]) -> None:
        """Send a single AX focus request; verification is owned by the runtime."""

        if not target.focusable or not guard():
            raise TextInputError("The editable field is no longer available for focus.")
        error = self.ax.AXUIElementSetAttributeValue(target.element, "AXFocused", True)
        if error != 0:
            raise TextInputError(f"macOS did not confirm field focus (AX error {error}).", effect_sent=True)

    def insert(self, target: TextField, text: str, guard: Callable[[], bool]) -> None:
        """Replace AXSelectedText or send paired Unicode events, never replay errors."""

        if not target.can_type or not text:
            raise TextInputError("The focused field has no readable text/caret baseline.")
        if len(expected_insertion(target.value, target.selection, text)) > MAX_VALUE_CHARACTERS:
            raise TextInputError("The insertion exceeds the local document verification limit.")
        controls = [character for character in text if unicodedata.category(character) == "Cc" or character in "\u2028\u2029"]
        if target.bundle_id == "com.apple.Terminal" and controls:
            raise TextInputError("Terminal typing cannot contain submission or control characters.")
        if controls and (not target.selected_text_writable or target.role != "AXTextArea" or any(c not in "\n\t\u2028\u2029" for c in controls)):
            raise TextInputError("This field cannot insert multiline/control text without keyboard actions.")
        if not guard():
            raise TextInputError("The foreground or focused editor changed before insertion.")
        if target.selected_text_writable:
            # Setter failure may still have changed text. Never fall back after dispatch.
            error = self.ax.AXUIElementSetAttributeValue(target.element, "AXSelectedText", text)
            if error != 0:
                raise TextInputError(f"Text insertion was uncertain (AX error {error}); it was not retried.", effect_sent=True)
            return
        if not self.quartz.CGPreflightPostEventAccess():
            raise TextInputError("macOS has not granted keyboard event posting permission.")
        events = []
        for units in unicode_chunks(text):
            pair = [self.quartz.CGEventCreateKeyboardEvent(None, 0, down) for down in (True, False)]
            if any(event is None for event in pair):
                raise TextInputError("macOS could not create Unicode keyboard events.")
            for event in pair:
                self.quartz.CGEventSetFlags(event, 0)
                self.quartz.CGEventKeyboardSetUnicodeString(event, len(units), units)
            events.append(pair)
        sent = False
        for down, up in events:
            # Focus guards deliberately do not copy the document value per batch.
            if not guard():
                raise TextInputError("Focus changed during insertion; partial text was not retried.", effect_sent=sent)
            self.quartz.CGEventPostToPid(target.pid, down)
            self.quartz.CGEventPostToPid(target.pid, up)
            sent = True
