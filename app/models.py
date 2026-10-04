"""Typed local records for executable app actions and validated semantic decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


# Limit provider payloads to values that can be safely encoded as JSON.
JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    """Bind a locally resolved operation and target to one concrete app effect."""

    id: str
    kind: str
    parameters: dict[str, JSONValue]
    description: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for terminal output."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class JevDecision:
    """Stores validated semantic factors and any locally executable action."""

    selected_candidate: ActionCandidate | None
    semantic: dict[str, JSONValue]
    probabilities: dict[str, dict[str, float]]
    factor_confidences: dict[str, float]
    confidence: float
    model: str
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_milliseconds: int

    def to_dict(self) -> dict[str, Any]:
        """Return the stable machine-readable decision payload."""

        return {
            "selected_candidate": self.selected_candidate.to_dict() if self.selected_candidate else None,
            "semantic": self.semantic,
            "probabilities": self.probabilities,
            "factor_confidences": self.factor_confidences,
            "confidence": self.confidence,
            "model": self.model,
            "request_id": self.request_id,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
            "latency_milliseconds": self.latency_milliseconds,
        }
