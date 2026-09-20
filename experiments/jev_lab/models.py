"""Typed local records for Jev Lab scenarios and validated decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


# Limits experiment payloads to values that can be safely encoded as JSON.
JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    """Binds an opaque model-facing ID to one concrete local action."""

    id: str
    kind: str
    parameters: dict[str, JSONValue]
    description: str

    def criterion(self) -> dict[str, JSONValue]:
        """Return structured Choice guidance without giving Jev executable authority."""

        # Parameters describe a pre-built action; they are not model-generated input.
        return {
            "action": self.kind,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for terminal output."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class MockScenario:
    """Pairs a mock transcription and desktop snapshot with allowed actions."""

    name: str
    description: str
    transcription: str
    desktop_state: dict[str, JSONValue]
    candidates: tuple[ActionCandidate, ...]


@dataclass(frozen=True, slots=True)
class JevDecision:
    """Stores a fully validated Jev choice and its provider metadata."""

    selected_candidate: ActionCandidate
    probabilities: dict[str, float]
    confidence: float
    model: str
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_milliseconds: int

    def to_dict(self) -> dict[str, Any]:
        """Return the stable machine-readable decision payload."""

        return {
            "selected_candidate": self.selected_candidate.to_dict(),
            "probabilities": dict(
                sorted(self.probabilities.items(), key=lambda item: item[1], reverse=True)
            ),
            "confidence": self.confidence,
            "model": self.model,
            "request_id": self.request_id,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
            "latency_milliseconds": self.latency_milliseconds,
        }
