"""Calls TypeSafe Jev and validates that it selected one supplied lab action."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from time import perf_counter_ns
from typing import Any, Protocol, cast

from typesafe_sdk import Choice, TypeSafeClient, TypeSafeError

from .models import ActionCandidate, JevDecision, JSONValue


# This key must match the Choice answer returned by System One.
QUESTION_ID = "next_action"
# Small tolerances account for floating-point rounding in provider responses.
PROBABILITY_SUM_TOLERANCE = 0.001
WINNER_TOLERANCE = 0.000_001


class JevCallError(RuntimeError):
    """Reports sanitized provider or response-validation failures."""


class SystemOneClient(Protocol):
    """Defines the SDK method needed by the adapter and its test doubles."""

    def system_one(
        self,
        state: Any,
        questions: Mapping[str, Any],
        *,
        model: str | None = None,
    ) -> Any: ...


def build_jev_state(
    transcription: str, desktop_state: Mapping[str, JSONValue]
) -> dict[str, JSONValue]:
    """Format mock speech and desktop data into the structured Jev state."""

    # Normalize only surrounding whitespace so the recorded request stays faithful.
    text = transcription.strip()
    if not text:
        raise JevCallError("The transcription cannot be empty.")
    return {
        "transcription": text,
        "desktop_state": dict(desktop_state),
    }


def build_choice(candidates: Sequence[ActionCandidate]) -> Choice:
    """Build the closed-set next-action question from local candidates."""

    if not candidates:
        raise JevCallError("At least one action candidate is required.")
    candidate_ids = [candidate.id for candidate in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise JevCallError("Action candidate IDs must be unique.")

    # Criteria are local facts; Jev may select an ID but cannot create an action.
    return Choice(
        instructions=(
            "Choose exactly one currently valid next action that best advances the "
            "transcribed user request. Choose STOP when the request is already complete "
            "or none of the other actions safely advances it. Do not invent actions, "
            "targets, or parameters."
        ),
        criteria={candidate.id: candidate.criterion() for candidate in candidates},
    )


def _validate_answer(
    answer: Any, candidates: Sequence[ActionCandidate]
) -> tuple[ActionCandidate, dict[str, float], float]:
    """Reject any answer that violates the local closed-set contract."""

    # Resolve choices against the exact candidates supplied for this one request.
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    choice = getattr(answer, "choice", None)
    if choice not in candidates_by_id:
        raise JevCallError("Jev selected an unknown action candidate ID.")

    raw_probabilities = getattr(answer, "probabilities", None)
    if not isinstance(raw_probabilities, Mapping):
        raise JevCallError("Jev did not return a probability distribution.")
    # Require a distribution over the closed set, with no omitted or invented IDs.
    if set(raw_probabilities) != set(candidates_by_id):
        raise JevCallError("Jev probability keys did not match the action candidate IDs.")

    probabilities: dict[str, float] = {}
    for candidate_id, raw_probability in raw_probabilities.items():
        if isinstance(raw_probability, bool) or not isinstance(raw_probability, (int, float)):
            raise JevCallError("Jev returned a non-numeric action probability.")
        probability = float(raw_probability)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise JevCallError("Jev returned an action probability outside [0, 1].")
        probabilities[str(candidate_id)] = probability

    if abs(sum(probabilities.values()) - 1.0) > PROBABILITY_SUM_TOLERANCE:
        raise JevCallError("Jev action probabilities did not sum to one.")

    raw_confidence = getattr(answer, "confidence", None)
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
        raise JevCallError("Jev did not return numeric confidence.")
    confidence = float(raw_confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise JevCallError("Jev confidence was outside [0, 1].")

    # A returned choice must agree with the distribution's most likely action.
    maximum = max(probabilities.values())
    if probabilities[choice] + WINNER_TOLERANCE < maximum:
        raise JevCallError("Jev's selected action was not a maximum-probability candidate.")

    return candidates_by_id[choice], probabilities, confidence


def _call_system_one(
    client: SystemOneClient,
    state: dict[str, JSONValue],
    choice: Choice,
    model: str,
) -> Any:
    """Make the one SDK request used for a mock scenario."""

    # Hide provider response bodies so errors cannot accidentally expose request data.
    try:
        return client.system_one(state, {QUESTION_ID: choice}, model=model)
    except TypeSafeError as error:
        status = getattr(error, "status", None)
        request_id = getattr(error, "request_id", None)
        details = [type(error).__name__]
        if status is not None:
            details.append(f"HTTP {status}")
        if request_id:
            details.append(f"request {request_id}")
        raise JevCallError("TypeSafe request failed (" + ", ".join(details) + ").") from error


def _decision_from_response(
    response: Any,
    candidates: Sequence[ActionCandidate],
    latency_milliseconds: int,
) -> JevDecision:
    """Convert a typed SDK response into the local validated decision."""

    choices = getattr(response, "choices", {})
    answer = choices.get(QUESTION_ID) if isinstance(choices, Mapping) else None
    if answer is None:
        raise JevCallError("TypeSafe response was missing the next_action Choice answer.")

    selected, probabilities, confidence = _validate_answer(answer, candidates)
    usage = getattr(response, "usage", None)
    return JevDecision(
        selected_candidate=selected,
        probabilities=probabilities,
        confidence=confidence,
        model=str(getattr(response, "model", "unknown")),
        request_id=getattr(response, "request_id", None),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        latency_milliseconds=latency_milliseconds,
    )


def select_action(
    transcription: str,
    desktop_state: Mapping[str, JSONValue],
    candidates: Sequence[ActionCandidate],
    *,
    api_key: str | None = None,
    model: str = "jev-latest",
    client: SystemOneClient | None = None,
) -> JevDecision:
    """Ask Jev to choose one candidate, then validate and resolve the choice locally."""

    # Build and validate all local inputs before a live SDK client is created.
    state = build_jev_state(transcription, desktop_state)
    choice = build_choice(candidates)
    started = perf_counter_ns()

    # Tests inject a fake client; live runs create and close the SDK client here.
    if client is not None:
        response = _call_system_one(client, state, choice, model)
    else:
        key = (api_key or "").strip()
        if not key:
            raise JevCallError("A TypeSafe API key is required.")
        try:
            with TypeSafeClient(api_key=key) as owned_client:
                response = _call_system_one(
                    cast(SystemOneClient, owned_client), state, choice, model
                )
        except TypeSafeError as error:
            # Initialization and context-manager failures use the same sanitized surface.
            status = getattr(error, "status", None)
            request_id = getattr(error, "request_id", None)
            details = [type(error).__name__]
            if status is not None:
                details.append(f"HTTP {status}")
            if request_id:
                details.append(f"request {request_id}")
            raise JevCallError(
                "TypeSafe request failed (" + ", ".join(details) + ")."
            ) from error

    # Keep latency as diagnostic metadata, separate from the provider's decision.
    latency_milliseconds = (perf_counter_ns() - started) // 1_000_000
    return _decision_from_response(response, candidates, latency_milliseconds)
