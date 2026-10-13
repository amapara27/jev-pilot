"""Apply deterministic local policy after Jev selects a supplied action."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .models import ActionCandidate
from .web_navigation import WEB_OPERATIONS, normalize_web_url
from .interactions import WINDOW_COMMANDS


def blocked_goal(goal: str) -> bool:
    """Keep credential, purchase, and destructive requests away from the provider."""

    return bool(
        re.search(
            r"\b(password|passcode|credit card|payment|purchase|buy|delete|erase|trash)\b",
            goal,
            re.IGNORECASE,
        )
    )


def disposition(action: ActionCandidate, confidence: float, *, auto_approve_basic: bool = False) -> str:
    """Return allow, confirm, or deny independently of the model's recommendation."""

    if action.kind == "STOP":
        return "deny"
    if action.kind in {'KEYBOARD_ACTION', 'SCROLL'}:
        if action.parameters.get('bundle_identifier') in {'com.apple.Terminal', 'com.googlecode.iterm2'} and action.kind == 'KEYBOARD_ACTION' and action.parameters.get('command') not in WINDOW_COMMANDS:
            return 'deny'
        return 'confirm' if action.parameters.get('requires_confirmation') or confidence < 0.82 else 'allow'
    if action.kind in WEB_OPERATIONS:
        url = action.parameters.get("url")
        if not isinstance(url, str) or normalize_web_url(url) != url:
            return "deny"
        return "allow" if auto_approve_basic or confidence >= 0.82 else "confirm"
    if action.kind == "PRESS_CONTROL":
        label = action.parameters.get("label", "")
        if blocked_goal(label) or action.parameters.get("bundle_identifier") == "com.apple.Terminal":
            return "deny"
        routine = label.casefold().strip(".…") in {"new", "new document", "new window", "new tab", "find", "search", "back", "next", "cancel", "preferences", "settings"}
        return "allow" if routine and confidence >= 0.82 else "confirm"
    if action.kind == "SEARCH" and (not action.parameters.get("text") or len(action.parameters["text"]) > 4096 or any(ord(c) < 32 or c in "\u2028\u2029" for c in action.parameters["text"])):
        return "deny"
    if action.kind == "OPEN_ITEM":
        path = Path(unquote(urlparse(action.parameters.get("url", "")).path))
        if path.suffix.casefold() in {".app", ".command", ".sh", ".workflow", ".scpt", ".scptd", ".pkg", ".dmg", ".webloc", ".url"}:
            return "deny"
    if action.kind not in {"OPEN_APP", "FOCUS_APP", "FOCUS_FIELD", "TYPE_TEXT", "CREATE_NOTE", "PREPARE_SEARCH", "SEARCH", "SELECT_ITEM", "OPEN_ITEM"}:
        return "deny"
    if action.kind in {"CREATE_NOTE", "SEARCH", "SELECT_ITEM", "OPEN_ITEM"}:
        return "allow" if confidence >= 0.82 or auto_approve_basic and action.kind == 'SEARCH' else "confirm"
    return "allow" if confidence >= 0.65 or auto_approve_basic and action.kind in {'OPEN_APP', 'FOCUS_APP', 'PREPARE_SEARCH'} else "confirm"
