"""Propose bounded text spans and resolve Jev's three choices without inventing effects."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .models import ActionCandidate, JSONValue
from .web_navigation import WEB_OPERATIONS, navigation_spans, normalize_web_url, search_url, suffix_search_span
from .interactions import PATTERNS, command_actions, action_operation


# Every operation stays visible even when parsing finds no matching verb or payload.
OPERATIONS = {
    "OPEN_APP": "Open a grounded installed app that is not running; no payload.",
    "FOCUS_APP": "Focus a running app that is not frontmost; no payload.",
    "OPEN_APP_FOR_TEXT": "Typing prerequisite: open a nonrunning app, then observe editors. Carry the intended text span (or none); do not insert yet.",
    "FOCUS_APP_FOR_TEXT": "Typing prerequisite: focus a running background app, then observe editors. Carry the intended text span (or none); do not insert yet.",
    "OPEN_APP_FOR_WORKFLOW": "Open a nonrunning app before a multi-step workflow. Carry the future text/query span (or none); do not insert yet.",
    "FOCUS_APP_FOR_WORKFLOW": "Focus a running background app before a multi-step workflow. Carry the future text/query span (or none); do not insert yet.",
    "FOCUS_FIELD": "Typing prerequisite: focus an observed focusable editable field that is not focused. Carry the intended span (or none); do not insert yet.",
    "TYPE_TEXT": "Insert an exact text span into a focused field with can_type=true, without submission. App-level targets cannot execute insertion.",
    "KEYBOARD_ACTION": "Perform an explicit supplied command in its observed editor/menu/window context. No payload. Follow next_command; maximize means enter full screen, minimize means minimize that window. Enter is separate from typing and unavailable in Terminal.",
    "SCROLL": "Send one wheel event to an observed scrollable area with remaining range. No payload. Follow next_command; never scroll without a grounded scroll target.",
    "OPEN_WEBSITE": "Open an exact named-site/domain/HTTP URL span in an installed browser. Choose the default_browser unless a browser is explicitly requested. Never insert into an editor.",
    "SEARCH_WEB": "Search a supplied website_search target (Google or YouTube) with an exact query span. Choose the default browser unless explicitly requested. Never use Finder search or type into an editor.",
    "PREPARE_SEARCH": "Activate Finder's observed Find menu to expose a search query field. Carry the query span (or none); do not enter it yet.",
    "SEARCH": "Set Finder's observed search query to an exact text span. Never type a search into a document.",
    "CREATE_NOTE": "Press Notes' uniquely observed New Note control. Carry the future typing span (or none), without insertion. Observe the new empty body before typing.",
    "PRESS_CONTROL": "Press an observed accessible button/menu in any app to advance the workflow. Carry future text (or none), without insertion. Verify a concrete control, editor or document change, then observe again.",
    "SELECT_ITEM": "Select one visible, uniquely named Finder item with an exact local file URL; no payload.",
    "OPEN_ITEM": "Open an already selected, uniquely named Finder item through its observed Open menu; no payload.",
    "RUN_COMMAND": "Explicitly submit a command in Terminal, distinct from typing. Not executable yet.",
    "STOP": "No action advances the request or it is already complete; target and payload must be none.",
    "UNSUPPORTED": "The requested operation is outside this menu, such as clicking; target and payload must be none.",
}
MAX_GOAL_CHARACTERS = 8192
MAX_PAYLOAD_SPANS = 16
# A carried span preserves later intent; only TYPE_TEXT/SEARCH dispatch its text.
PREPARATION_OPERATIONS = {
    "OPEN_APP_FOR_TEXT", "FOCUS_APP_FOR_TEXT", "OPEN_APP_FOR_WORKFLOW",
    "FOCUS_APP_FOR_WORKFLOW", "FOCUS_FIELD", "CREATE_NOTE", "PREPARE_SEARCH",
    "PRESS_CONTROL",
}


class SemanticError(ValueError):
    """Indicates an incompatible choice or an exceeded local extraction bound."""


@dataclass(frozen=True, slots=True)
class SemanticMenu:
    """Keep the exact local target and payload facts used by one Jev request."""

    goal: str
    targets: dict[str, dict[str, JSONValue]]
    payloads: dict[str, dict[str, JSONValue]]
    intent: dict = field(default_factory=dict)

    def resolve(
        self, operation: str, target_id: str, payload_id: str
    ) -> tuple[dict[str, JSONValue], ActionCandidate | None]:
        """Check compatibility before materializing one grounded native effect."""

        target, payload = self.targets[target_id], self.payloads[payload_id]
        if self.intent:
            if operation not in self.intent['operations']:
                raise SemanticError('This operation is outside the requested app/workflow scope.')
            bundle = target_id if target.get('scope') == 'application' else target.get('bundle_identifier')
            if target_id != 'none' and bundle != self.intent['bundle']:
                raise SemanticError('The selected app does not match the explicit command/workflow.')
        if operation in {"STOP", "UNSUPPORTED"}:
            valid = target_id == payload_id == "none"
        elif operation in {"OPEN_APP", "FOCUS_APP", "OPEN_APP_FOR_TEXT", "FOCUS_APP_FOR_TEXT", "OPEN_APP_FOR_WORKFLOW", "FOCUS_APP_FOR_WORKFLOW"}:
            valid = target.get("scope") == "application" and not target["active"]
            valid = valid and (operation in PREPARATION_OPERATIONS or payload_id == "none")
            valid = valid and bool(target["running"]) == operation.startswith("FOCUS_APP")
        elif operation == "FOCUS_FIELD":
            valid = target.get("scope") == "field"
            valid = valid and target["focusable"] and not target["focused"]
        elif operation == "TYPE_TEXT":
            valid = target.get("scope") == "field" and payload_id != "none" and target["can_type"]
        elif operation in {'KEYBOARD_ACTION', 'SCROLL'}:
            valid = target.get('scope') == 'interaction' and payload_id == 'none' and action_operation(target['command']) == operation
        elif operation == "OPEN_WEBSITE":
            valid = target.get("scope") == "application" and target.get("is_browser") and normalize_web_url(payload.get("text")) is not None
        elif operation == "SEARCH_WEB":
            valid = target.get("scope") == "website_search" and search_url(target.get("site"), payload.get("text")) is not None
            suffix = suffix_search_span(self.goal)
            if suffix:
                valid = valid and (payload.get('start'), payload.get('end')) == suffix[1:]
        elif operation == "CREATE_NOTE":
            valid = target_id == "com.apple.Notes"
        elif operation == "PRESS_CONTROL":
            valid = target.get("scope") == "control"
        elif operation == "PREPARE_SEARCH":
            valid = target_id == "com.apple.finder"
        elif operation == "SEARCH":
            valid = target.get("scope") == "application" and payload_id != "none"
        elif operation in {"SELECT_ITEM", "OPEN_ITEM"}:
            valid = target.get("scope") == "finder_item" and not target.get("ambiguous") and payload_id == "none"
            if operation == "OPEN_ITEM":
                valid = valid and target.get("selected") and target.get("sole_selected")
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
        if operation in {'KEYBOARD_ACTION', 'SCROLL'}:
            action = ActionCandidate(f'{operation}:{target_id}', operation,
                                     {'element_id': target_id, 'bundle_identifier': target['bundle_identifier'],
                                      'command': target['command'], 'requires_confirmation': target['requires_confirmation']},
                                     f"{target['label']} in the focused app.")
        elif operation in {"OPEN_APP", "FOCUS_APP", "OPEN_APP_FOR_TEXT", "FOCUS_APP_FOR_TEXT", "OPEN_APP_FOR_WORKFLOW", "FOCUS_APP_FOR_WORKFLOW"}:
            kind = "FOCUS_APP" if operation.startswith("FOCUS_APP") else "OPEN_APP"
            parameters = {"bundle_identifier": target_id, "name": target["name"]}
            if operation.endswith("_FOR_TEXT"):
                parameters["continue_typing"] = True
            if operation.endswith("_FOR_WORKFLOW"):
                parameters["continue_workflow"] = True
            required = required_workflow_operations(self.goal)
            if required:
                parameters["continue_workflow" if required - {"TYPE_TEXT"} else "continue_typing"] = True
            action = ActionCandidate(
                id=f"{operation}:{target_id}",
                kind=kind,
                parameters=parameters,
                description=f"{'Open' if kind == 'OPEN_APP' else 'Focus'} {target['name']}.",
            )
        elif operation in WEB_OPERATIONS:
            url = normalize_web_url(payload["text"]) if operation == "OPEN_WEBSITE" else search_url(target["site"], payload["text"])
            action = ActionCandidate(f"{operation}:{target_id}:{payload_id}", operation,
                                    {"bundle_identifier": target["bundle_identifier"], "url": url},
                                    f"Open {url} in {target.get('browser', target.get('name'))}.")
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
        elif operation in {"CREATE_NOTE", "PREPARE_SEARCH", "SEARCH", "SELECT_ITEM", "OPEN_ITEM", "PRESS_CONTROL"}:
            bundle = target.get("bundle_identifier")
            controls = self.targets.get(bundle, {}).get("workflow_controls", {})
            identifier = {"CREATE_NOTE": controls.get("new_note"), "PREPARE_SEARCH": controls.get("find"),
                          "SEARCH": controls.get("query"), "SELECT_ITEM": target_id, "OPEN_ITEM": target_id, "PRESS_CONTROL": target_id}[operation]
            if operation == "SEARCH" and bundle != "com.apple.finder":
                identifier = None
            if identifier and (operation != "OPEN_ITEM" or controls.get("open")):
                parameters = {"bundle_identifier": bundle, "element_id": identifier}
                if operation == "SEARCH":
                    parameters["text"] = payload["text"]
                if operation == "OPEN_ITEM":
                    parameters.update(url=target["url"], open_control_id=controls["open"])
                description = {"CREATE_NOTE": "Create a new note in Notes.", "PREPARE_SEARCH": "Prepare Finder search."}.get(operation)
                if operation == "SEARCH":
                    description = f"Search Finder for {payload['text']!r}."
                if operation in {"SELECT_ITEM", "OPEN_ITEM"}:
                    description = f"{'Select' if operation == 'SELECT_ITEM' else 'Open'} {target['label']!r} ({target['url']})."
                if operation == "PRESS_CONTROL":
                    parameters["label"] = target["label"]
                    description = f"Press {target['label']!r} in the active app."
                action = ActionCandidate(f"{operation}:{identifier}", operation, parameters, description)
        return semantic, action

    def operation_criteria(self) -> dict[str, dict[str, JSONValue]]:
        """Keep all intents visible while identifying targets valid for the current step."""

        span = next((key for key in self.payloads if key != "none"), "none")
        suffix = suffix_search_span(self.goal)
        query_ids = [key for key, payload in self.payloads.items() if suffix and (payload.get('start'), payload.get('end')) == suffix[1:]]
        criteria = {}
        for operation, description in OPERATIONS.items():
            payload_ids = [key for key in self.payloads if key != "none"]
            if operation == "OPEN_WEBSITE":
                payload_ids = [key for key in payload_ids if normalize_web_url(self.payloads[key]["text"])]
            elif operation == "SEARCH_WEB":
                payload_ids = [key for key in payload_ids if search_url("google", self.payloads[key]["text"])]
                if suffix:
                    payload_ids = query_ids
            payload = span if operation in {"TYPE_TEXT", "SEARCH", "RUN_COMMAND"} else "none"
            if operation in WEB_OPERATIONS:
                payload = next(iter(payload_ids), "none")
            targets = []
            for identifier in self.targets:
                try:
                    _, action = self.resolve(operation, identifier, payload)
                except SemanticError:
                    continue
                # App-level typing is describable but is never an executable next step.
                if operation not in {"TYPE_TEXT", "CREATE_NOTE", "PREPARE_SEARCH", "SEARCH", "SELECT_ITEM", "OPEN_ITEM"} or action is not None:
                    targets.append(identifier)
            if operation in PREPARATION_OPERATIONS:
                payload_ids = ['none', *query_ids] if suffix else list(self.payloads)
            elif operation not in {"TYPE_TEXT", "SEARCH", "RUN_COMMAND"} | WEB_OPERATIONS:
                payload_ids = ["none"]
            criteria[operation] = {"description": description, "available_targets": targets,
                                   "available_payloads": payload_ids}
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
        r"\b(?:type(?:\s+command)?|write|dictate|insert|(?:search|find|locate)(?:\s+(?:in\s+)?(?:Finder|Google|YouTube|(?:the\s+)?web))?(?:\s+for)?|google|run(?:\s+command)?)\s+",
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
            r"\s+(?:(?:in|using|with)\s+(?:a\s+new\s+note|[\w ]+)|and\s+(?:then\s+)?(?:open|switch|type|write|search|run|press|hit|select|scroll|copy|cut|paste|undo|redo|minimi[zs]e|maximi[zs]e|enter\s+full\s*screen)\b)",
            goal[start:],
            re.IGNORECASE,
        ):
            add(start, start + boundary.start())
    suffix = suffix_search_span(goal)
    if suffix:
        add(suffix[1], suffix[2])
    for start, end in navigation_spans(goal):
        add(start, end)
    return {
        "none": {"description": "No payload."},
        **{
            f"span_{index}": {"start": start, "end": end, "text": goal[start:end]}
            for index, (start, end) in enumerate(spans)
        },
    }


def required_workflow_operations(goal: str) -> set[str]:
    """Recognize supported completion requirements while shielding quoted/typed text."""

    command = re.sub(r'"(?:\\.|[^"\\])*"|“[^”]*”|(?<!\w)\x27[^\x27]*\x27(?!\w)', lambda match: " " * len(match.group()), goal)
    typing = re.search(r"\b(?:type|write|dictate|insert)\s+", command, re.IGNORECASE)
    search = re.search(r"\b(?:google\s+|search\s+(?:(?:the\s+)?web|google|youtube)(?:\s+for)?\s+)", command, re.IGNORECASE)
    web_search = search is not None and (typing is None or search.start() < typing.start())
    suffix = suffix_search_span(goal)
    web_search = web_search or suffix is not None
    required = {"SEARCH_WEB"} if web_search else {"TYPE_TEXT"} if typing else set()
    commands = command_actions(goal)
    required.update(action_operation(item) for item in commands)
    if not web_search and "in a new note" in command.casefold():
        required.add("CREATE_NOTE")
    boundary = suffix[0] if suffix else search.start() if web_search else typing.start() if typing else len(goal)
    command, source = command[:boundary].casefold(), goal[:boundary]
    for _, pattern in PATTERNS:
        command = re.sub(r'\b' + pattern + r'\b', ' ', command, flags=re.I)
    web_open = any(normalize_web_url(source[start:end]) for start, end in navigation_spans(source))
    if web_open:
        required.add("OPEN_WEBSITE")
    if re.search(r"\b(?:(?:create|make|start)\s+(?:a\s+)?(?:new\s+)?note|open\s+(?:a\s+)?new\s+note)\b", command):
        required.add("CREATE_NOTE")
    if re.search(r"\b(?:click|press|choose)\b|\b(?:create|make|open)\s+(?:a\s+)?new\s+(?:document|window|tab)\b", command):
        required.add("PRESS_CONTROL")
    if not web_search and re.search(r"\b(?:search|find|locate)\b", command):
        required.add("SEARCH")
    if re.search(r"\bselect\b", command):
        required.add("SELECT_ITEM")
    if not web_open and re.search(r"\bopen\s+(?:it|this|that|the\s+(?:file|result)|[\w.-]+\.[a-z0-9]+)\b", command):
        required.add("OPEN_ITEM")
    return required
