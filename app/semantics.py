"""Propose bounded text spans and resolve Jev's three choices without inventing effects."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .models import ActionCandidate, JSONValue


# Every operation stays visible even when parsing finds no matching verb or payload.
OPERATIONS = {
    "OPEN_APP": "Open a grounded installed app that is not running; no payload.",
    "FOCUS_APP": "Focus a running app that is not frontmost; no payload.",
    "OPEN_APP_FOR_TEXT": "Typing prerequisite: open a nonrunning app, then observe editors. Carry the intended text span (or none); do not insert yet.",
    "FOCUS_APP_FOR_TEXT": "Typing prerequisite: focus a running background app, then observe editors. Carry the intended text span (or none); do not insert yet.",
    "FOCUS_FIELD": "Typing prerequisite: focus an observed focusable editable field that is not focused. Carry the intended span (or none); do not insert yet.",
    "TYPE_TEXT": "Insert an exact text span into a focused field with can_type=true, without submission. App-level targets cannot execute insertion.",
    "SEARCH": "Search within a grounded app for an exact text span. Not executable yet.",
    "CREATE_NOTE": "Create a new note in Notes; no payload. Not executable yet.",
    "RUN_COMMAND": "Explicitly submit a command in Terminal, distinct from typing. Not executable yet.",
    "STOP": "No action advances the request or it is already complete; target and payload must be none.",
    "UNSUPPORTED": "The requested operation is outside this menu, such as clicking; target and payload must be none.",
}
MAX_GOAL_CHARACTERS = 8192
MAX_PAYLOAD_SPANS = 16


class SemanticError(ValueError):
    """Indicates an incompatible choice or an exceeded local extraction bound."""


@dataclass(frozen=True, slots=True)
class SemanticMenu:
    """Keep the exact local target and payload facts used by one Jev request."""

    goal: str
    targets: dict[str, dict[str, JSONValue]]
    payloads: dict[str, dict[str, JSONValue]]

    def resolve(
        self, operation: str, target_id: str, payload_id: str
    ) -> tuple[dict[str, JSONValue], ActionCandidate | None]:
        """Check compatibility before materializing an executable app activation."""

        target, payload = self.targets[target_id], self.payloads[payload_id]
        if operation in {"STOP", "UNSUPPORTED"}:
            valid = target_id == payload_id == "none"
        elif operation in {"OPEN_APP", "FOCUS_APP", "OPEN_APP_FOR_TEXT", "FOCUS_APP_FOR_TEXT"}:
            valid = target.get("scope") == "application" and not target["active"]
            valid = valid and (operation.endswith("_FOR_TEXT") or payload_id == "none")
            valid = valid and bool(target["running"]) == operation.startswith("FOCUS_APP")
        elif operation == "FOCUS_FIELD":
            valid = target.get("scope") == "field"
            valid = valid and target["focusable"] and not target["focused"]
        elif operation == "TYPE_TEXT":
            valid = target_id != "none" and payload_id != "none"
            if target.get("scope") == "field":
                valid = valid and target["can_type"]
        elif operation == "CREATE_NOTE":
            valid = target_id == "com.apple.Notes" and payload_id == "none"
        else:
            valid = target_id != "none" and payload_id != "none"
            if operation == "RUN_COMMAND":
                valid = valid and target_id == "com.apple.Terminal"
        if not valid:
            raise SemanticError(
                "Jev selected incompatible operation, target, and payload choices: "
                f"operation={operation}, target={target_id}, payload={payload_id}."
            )

        semantic = {
            "operation": operation,
            "target_id": target_id,
            "target": target,
            "payload_id": payload_id,
            "payload": payload,
        }
        action = None
        if operation in {"OPEN_APP", "FOCUS_APP", "OPEN_APP_FOR_TEXT", "FOCUS_APP_FOR_TEXT"}:
            kind = "FOCUS_APP" if operation.startswith("FOCUS_APP") else "OPEN_APP"
            parameters = {"bundle_identifier": target_id, "name": target["name"]}
            if operation.endswith("_FOR_TEXT"):
                parameters["continue_typing"] = True
            action = ActionCandidate(
                id=f"{operation}:{target_id}",
                kind=kind,
                parameters=parameters,
                description=f"{'Open' if kind == 'OPEN_APP' else 'Focus'} {target['name']}.",
            )
        elif operation in {"TYPE_TEXT", "FOCUS_FIELD"} and target.get("scope") == "field":
            parameters = {
                "element_id": target_id, "bundle_identifier": target["bundle_identifier"],
                "process_identifier": target["process_identifier"],
            }
            if operation == "TYPE_TEXT":
                parameters["text"] = payload["text"]
            action = ActionCandidate(
                id=f"{operation}:{target_id}", kind=operation, parameters=parameters,
                description=f"{'Type exact text into' if operation == 'TYPE_TEXT' else 'Focus'} {target['label']}.",
            )
        return semantic, action

    def operation_criteria(self) -> dict[str, dict[str, JSONValue]]:
        """Keep all intents visible while identifying targets valid for the current step."""

        span = next((key for key in self.payloads if key != "none"), "none")
        criteria = {}
        for operation, description in OPERATIONS.items():
            payload = span if operation in {"TYPE_TEXT", "SEARCH", "RUN_COMMAND"} else "none"
            targets = []
            for identifier in self.targets:
                try:
                    _, action = self.resolve(operation, identifier, payload)
                except SemanticError:
                    continue
                # App-level typing is describable but is never an executable next step.
                if operation != "TYPE_TEXT" or action is not None:
                    targets.append(identifier)
            criteria[operation] = {"description": description, "available_targets": targets}
        return criteria


def extract_payloads(goal: str) -> dict[str, dict[str, JSONValue]]:
    """Offer exact quote interiors and plausible command suffixes with source offsets."""

    if not goal.strip():
        raise SemanticError("The transcription cannot be empty.")
    if len(goal) > MAX_GOAL_CHARACTERS:
        raise SemanticError("The goal exceeds the local 8192-character limit.")
    spans: list[tuple[int, int]] = []

    def add(start: int, end: int) -> None:
        if start < end and (start, end) not in spans:
            spans.append((start, end))
        if len(spans) > MAX_PAYLOAD_SPANS:
            raise SemanticError("The payload proposals exceed the local 16-span limit.")

    # Offsets refer to Python Unicode code points, not UTF-16 or encoded bytes.
    quotes = list(re.finditer(r'"(?:\\.|[^"\\])*"|“[^”]*”|(?<!\w)\x27[^\x27]*\x27(?!\w)', goal))
    for match in quotes:
        add(match.start() + 1, match.end() - 1)
    # ponytail: narrow English span proposals; add forms when real workflows need them.
    verbs = re.finditer(
        r"\b(?:type(?:\s+command)?|write|dictate|insert|search(?:\s+for)?|find|locate|run(?:\s+command)?)\s+",
        goal,
        re.IGNORECASE,
    )
    for match in verbs:
        start = match.end()
        if any(quote.start() <= start < quote.end() for quote in quotes):
            continue
        add(start, len(goal))
        # Keep the full suffix AND alternatives: Jev decides whether trailing words
        # are literal text, an app context, a prerequisite, or another command.
        for boundary in re.finditer(
            r"\s+(?:in\s+(?:a\s+new\s+note|[\w ]+)|and\s+(?:then\s+)?(?:open|switch|type|write|search|run)\b)",
            goal[start:],
            re.IGNORECASE,
        ):
            add(start, start + boundary.start())
    return {
        "none": {"description": "No payload."},
        **{
            f"span_{index}": {"start": start, "end": end, "text": goal[start:end]}
            for index, (start, end) in enumerate(spans)
        },
    }
