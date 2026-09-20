"""Deterministic Jev Lab transcripts, desktop states, and action sets."""

from __future__ import annotations

from .models import ActionCandidate, MockScenario


def _candidate(
    identifier: str,
    kind: str,
    description: str,
    **parameters: str,
) -> ActionCandidate:
    """Build one compact fixture candidate."""

    return ActionCandidate(identifier, kind, parameters, description)


# Each fixture is synthetic: it documents an input/allowed-output boundary without macOS access.
SCENARIOS: dict[str, MockScenario] = {
    "focus-terminal": MockScenario(
        name="focus-terminal",
        description="Terminal is running behind Safari and can be focused.",
        transcription="Switch to Terminal.",
        desktop_state={
            "active_application": {
                "name": "Safari",
                "bundle_identifier": "com.apple.Safari",
            },
            "running_applications": [
                {"name": "Safari", "bundle_identifier": "com.apple.Safari"},
                {"name": "Terminal", "bundle_identifier": "com.apple.Terminal"},
            ],
            "focused_window": {"id": "window-safari", "title": "TypeSafe Docs"},
            "elements": [],
        },
        candidates=(
            _candidate(
                "action_0",
                "FOCUS_APP",
                "Bring the already-running Terminal application to the foreground.",
                bundle_identifier="com.apple.Terminal",
                name="Terminal",
            ),
            _candidate(
                "action_1",
                "STOP",
                "Stop because the request is complete or cannot be safely advanced.",
                reason="goal complete or no safe valid action remains",
            ),
        ),
    ),
    "click-control": MockScenario(
        name="click-control",
        description="A visible enabled Run Tests button is available in the editor.",
        transcription="Click Run Tests.",
        desktop_state={
            "active_application": {
                "name": "Visual Studio Code",
                "bundle_identifier": "com.microsoft.VSCode",
            },
            "focused_window": {"id": "window-editor", "title": "jev-pilot"},
            "elements": [
                {
                    "id": "element-run-tests",
                    "role": "AXButton",
                    "label": "Run Tests",
                    "enabled": True,
                    "focused": False,
                    "supported_actions": ["AXPress"],
                }
            ],
        },
        candidates=(
            _candidate(
                "action_0",
                "CLICK_ELEMENT",
                "Activate the visible enabled button labeled Run Tests.",
                element_id="element-run-tests",
                label="Run Tests",
            ),
            _candidate(
                "action_1",
                "FOCUS_ELEMENT",
                "Move keyboard focus to the Run Tests button without activating it.",
                element_id="element-run-tests",
                label="Run Tests",
            ),
            _candidate(
                "action_2",
                "STOP",
                "Stop because the request is complete or cannot be safely advanced.",
                reason="goal complete or no safe valid action remains",
            ),
        ),
    ),
    "type-text": MockScenario(
        name="type-text",
        description="A search field is focused and ready for exact user-provided text.",
        transcription='Type "TypeSafe Jev documentation".',
        desktop_state={
            "active_application": {
                "name": "Safari",
                "bundle_identifier": "com.apple.Safari",
            },
            "focused_window": {"id": "window-safari", "title": "New Tab"},
            "focused_element_id": "element-address",
            "elements": [
                {
                    "id": "element-address",
                    "role": "AXTextField",
                    "label": "Address and Search",
                    "value": "",
                    "enabled": True,
                    "focused": True,
                }
            ],
        },
        candidates=(
            _candidate(
                "action_0",
                "TYPE_TEXT",
                "Type the exact quoted user text into the focused Address and Search field.",
                element_id="element-address",
                text="TypeSafe Jev documentation",
            ),
            _candidate(
                "action_1",
                "PRESS_KEY",
                "Press Return in the active application.",
                key="return",
            ),
            _candidate(
                "action_2",
                "STOP",
                "Stop because the request is complete or cannot be safely advanced.",
                reason="goal complete or no safe valid action remains",
            ),
        ),
    ),
    "stop-complete": MockScenario(
        name="stop-complete",
        description="Safari already displays the requested TypeSafe documentation.",
        transcription="Open the TypeSafe documentation in Safari.",
        desktop_state={
            "active_application": {
                "name": "Safari",
                "bundle_identifier": "com.apple.Safari",
            },
            "focused_window": {"id": "window-safari", "title": "TypeSafe AI Docs"},
            "elements": [
                {
                    "id": "element-page-title",
                    "role": "AXStaticText",
                    "label": "TypeSafe AI Documentation",
                    "value": "Introduction",
                    "enabled": True,
                    "focused": False,
                }
            ],
        },
        candidates=(
            _candidate(
                "action_0",
                "SCROLL_DOWN",
                "Scroll down to reveal later content on the current documentation page.",
            ),
            _candidate(
                "action_1",
                "STOP",
                "Stop because Safari already displays the requested TypeSafe documentation.",
                reason="goal already complete",
            ),
        ),
    ),
}


def get_scenario(name: str) -> MockScenario:
    """Return one named fixture or raise a concise lookup error."""

    try:
        return SCENARIOS[name]
    except KeyError as error:
        available = ", ".join(sorted(SCENARIOS))
        raise KeyError(f"Unknown fixture {name!r}. Available fixtures: {available}") from error
