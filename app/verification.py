"""Verify activation, exact text and scoped workflow effects without replay."""

from __future__ import annotations

from time import monotonic, sleep

from .models import ActionCandidate
from .text_input import expected_insertion, utf16_length


def editor_readiness(state: object) -> dict:
    """Expose bounded capability facts without document values, carets or handles."""

    return {"focused_window_available": state.focused_window_available,
            "field_count": len(state.text_fields),
            "can_type_count": sum(item.can_type for item in state.text_fields),
            **state.editor_diagnostics}


def note_body(state: object):
    """Bind creation to one writable Notes body, never a search field or guessed editor."""

    fields = [item for item in state.text_fields if item.bundle_id == "com.apple.Notes"
              and item.role == "AXTextArea" and (item.focusable or item.focused)
              and (item.selected_text_writable or item.keyboard_writable)]
    return fields[0] if len(fields) == 1 else None


def selected_notes(state: object) -> set[str]:
    """Use native selected-row identity independently of editor focus."""

    return {item.id for item in state.workflow_targets if item.kind == "note_selection"}


def same_created_note(desktop: object, created: object, state: object) -> bool:
    """Keep later focus/typing bound to the new note, even when the body handle is reused."""

    original, current = note_body(created), note_body(state)
    return bool(created.active == state.active and original and current
                and desktop.text.same_target(original, current, typing=False)
                and selected_notes(created) == selected_notes(state))


def verify_app(desktop: object, action: ActionCandidate, timeout_seconds: float = 3) -> bool:
    """Keep app-only callers on the same native outcome verification path."""

    return verify_action(desktop, action, None, timeout_seconds)


def verify_action(desktop: object, action: ActionCandidate, before: object, timeout_seconds: float = 3,
                  *, observations: list | None = None, diagnostics: dict | None = None, cancel_event=None) -> bool:
    """Wait for preparation readiness or the exact effect, without replaying dispatch."""

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
    details = diagnostics if diagnostics is not None else {}
    while True:
        if cancel_event is not None and cancel_event.is_set():
            details["reason"] = "cancelled"
            return False
        state = desktop.snapshot()
        if observations is not None:
            observations[:] = [state]
        details.update(reason="effect_not_observed", editor_readiness=editor_readiness(state))
        if action.kind == "OPEN_ITEM" and state.document_url == action.parameters["url"]:
            details["reason"] = "destination_document_observed"
            return True
        if state.active and state.active.bundle_id == expected_bundle:
            if action.kind in {"OPEN_APP", "FOCUS_APP"}:
                # Both preparation paths need an AX window, not just a frontmost app.
                if state.focused_window_available or not (action.parameters.get("continue_typing") or action.parameters.get("continue_workflow")):
                    details["reason"] = "app_ready"
                    return True
            elif action.kind == "CREATE_NOTE":
                old_notes, new_notes = selected_notes(before), selected_notes(state)
                current = note_body(state)
                details["reason"] = "note_identity_unproven" if current else "note_body_missing_or_ambiguous"
                if current:
                    details.update(body_focused=current.focused, body_empty=current.value == "" if current.value is not None else None,
                                   caret_at_start=current.selection == (0, 0) if current.selection is not None else None)
                    previous = next((item for item in before.text_fields if item.id == current.id), None)
                    if len(new_notes) == 1 and new_notes != old_notes:
                        details["reason"] = "note_selection_changed"
                        return True
                    if previous is None and note_body(before) is not None and current.can_type and current.value == "" and current.selection == (0, 0) and not desktop.text.equal(note_body(before).element, current.element):
                        details["reason"] = "note_editor_changed"
                        return True
                    if current.can_type and current.value == "" and current.selection == (0, 0) and previous and (previous.value not in {None, ""} or not desktop.text.equal(previous.window, current.window)):
                        details["reason"] = "new_empty_body"
                        return True
            elif action.kind == "PREPARE_SEARCH":
                if any(item.kind == "query" for item in state.workflow_targets):
                    details["reason"] = "search_query_available"
                    return True
            elif action.kind == "PRESS_CONTROL":
                details["reason"] = "control_effect_unobserved"
                previous = next(item for item in before.workflow_targets if item.id == action.parameters["element_id"])
                current = next((item for item in state.workflow_targets if item.id == previous.id), None)
                if current and desktop.text.equal(previous.element, current.element) and desktop.text.equal(previous.window, current.window):
                    if current.selected != previous.selected or current.expanded != previous.expanded:
                        details["reason"] = "control_state_changed"
                        return True
                old_fields = {item.id for item in before.text_fields}
                if any(item.id not in old_fields for item in state.text_fields):
                    details["reason"] = "editor_changed"
                    return True
                if state.document_url and state.document_url != before.document_url:
                    details["reason"] = "document_changed"
                    return True
            elif action.kind in {"SEARCH", "SELECT_ITEM"}:
                identifier = action.parameters["element_id"]
                previous = next((item for item in before.workflow_targets if item.id == identifier), None)
                current = next((item for item in state.workflow_targets if item.id == identifier), None)
                if previous and current and desktop.text.equal(previous.element, current.element) and desktop.text.equal(previous.window, current.window):
                    if action.kind == "SEARCH" and current.value == action.parameters["text"]:
                        details["reason"] = "query_entered"
                        return True
                    if action.kind == "SELECT_ITEM" and current.selected and current.sole_selected and current.url == previous.url and current.identity == previous.identity:
                        details["reason"] = "sole_item_selected"
                        return True
            elif action.kind in {"TYPE_TEXT", "FOCUS_FIELD"}:
                current = next((item for item in state.text_fields if item.id == original.id), None)
                if current and current.focused and desktop.text.same_target(original, current, typing=False):
                    if action.kind == "FOCUS_FIELD":
                        details["reason"] = "editor_focused"
                        return True
                    if current.value == expected_value and current.selection == expected_caret:
                        details["reason"] = "exact_insertion_observed"
                        return True
        if before is not None and state.active != before.active and action.kind not in {"OPEN_APP", "FOCUS_APP", "OPEN_ITEM"}:
            details["reason"] = "foreground_app_changed"
            return False
        if monotonic() >= deadline:
            return False
        if cancel_event is not None:
            cancel_event.wait(0.05)
        else:
            sleep(0.05)
