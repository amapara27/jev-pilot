"""Verify app activation, field focus, and exact text replacement without replay."""

from __future__ import annotations

from time import monotonic, sleep

from .models import ActionCandidate
from .text_input import expected_insertion, utf16_length


def verify_app(desktop: object, action: ActionCandidate, timeout_seconds: float = 3) -> bool:
    """Keep app-only callers on the same native outcome verification path."""

    return verify_action(desktop, action, None, timeout_seconds)


def verify_action(desktop: object, action: ActionCandidate, before: object, timeout_seconds: float = 3) -> bool:
    """Require full expected text and caret state on the same native editor/window."""

    expected_bundle = action.parameters["bundle_identifier"]
    original = None
    expected_value = expected_caret = None
    if action.kind in {"TYPE_TEXT", "FOCUS_FIELD"}:
        original = next(item for item in before.text_fields if item.id == action.parameters["element_id"])
        if action.kind == "TYPE_TEXT":
            text = action.parameters["text"]
            expected_value = expected_insertion(original.value, original.selection, text)
            expected_caret = (original.selection[0] + utf16_length(text), 0)
    deadline = monotonic() + timeout_seconds
    while True:
        state = desktop.snapshot()
        if state.active and state.active.bundle_id == expected_bundle:
            if action.kind in {"OPEN_APP", "FOCUS_APP"}:
                if not action.parameters.get("continue_typing") or state.focused_window_available:
                    return True
            else:
                current = next((item for item in state.text_fields if item.id == original.id), None)
                if current and current.focused and desktop.text.same_target(original, current, typing=False):
                    if action.kind == "FOCUS_FIELD":
                        return True
                    if current.value == expected_value and current.selection == expected_caret:
                        return True
        if monotonic() >= deadline:
            return False
        sleep(0.05)
