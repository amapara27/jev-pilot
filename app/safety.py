"""Apply deterministic local policy after Jev selects a supplied action."""

from __future__ import annotations

import re

from .models import ActionCandidate


def blocked_goal(goal: str) -> bool:
    """Keep credential, purchase, and destructive requests away from the provider."""

    return bool(
        re.search(
            r"\b(password|passcode|credit card|payment|purchase|buy|delete|erase|trash)\b",
            goal,
            re.IGNORECASE,
        )
    )


def disposition(action: ActionCandidate, confidence: float) -> str:
    """Return allow, confirm, or deny independently of the model's recommendation."""

    if action.kind == "STOP":
        return "deny"
    if action.kind not in {"OPEN_APP", "FOCUS_APP"}:
        return "deny"
    return "allow" if confidence >= 0.65 else "confirm"
