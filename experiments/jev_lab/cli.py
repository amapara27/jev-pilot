"""Command-line interface for one-call Jev Lab experiments."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TextIO

from .client import JevCallError, build_jev_state, select_action
from .config import ConfigurationError, load_api_key
from .fixtures import SCENARIOS, get_scenario
from .models import JevDecision, MockScenario


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit one-operation command-line contract."""

    parser = argparse.ArgumentParser(
        description="Send one synthetic desktop scenario to TypeSafe Jev without executing it."
    )
    # Keep the harness to either inspection or one deliberate provider request.
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--list", action="store_true", help="List available fixtures.")
    operation.add_argument("--fixture", metavar="NAME", help="Run exactly one fixture.")
    parser.add_argument("--model", default="jev-latest", help="TypeSafe model or alias.")
    return parser


def result_payload(scenario: MockScenario, decision: JevDecision) -> dict[str, object]:
    """Combine the mock input and validated decision into one JSON payload."""

    # Preserve enough input and output to reproduce the experiment from its terminal log.
    return {
        "fixture": scenario.name,
        "transcription": scenario.transcription,
        "jev_state": build_jev_state(scenario.transcription, scenario.desktop_state),
        "candidates": [candidate.to_dict() for candidate in scenario.candidates],
        "decision": decision.to_dict(),
    }


def print_result(
    scenario: MockScenario, decision: JevDecision, output: TextIO = sys.stdout
) -> None:
    """Print a readable summary followed by the complete JSON result."""

    print(f"Fixture: {scenario.name}", file=output)
    print(f"Description: {scenario.description}", file=output)
    print(f"Transcription: {scenario.transcription}", file=output)
    print("Desktop state:", file=output)
    print(json.dumps(scenario.desktop_state, indent=2, sort_keys=True), file=output)
    print("Candidates:", file=output)
    for candidate in scenario.candidates:
        print(f"  {candidate.id}: {candidate.kind} — {candidate.description}", file=output)

    # Print the chosen local action before the complete machine-readable payload.
    selected = decision.selected_candidate
    print(f"Selected: {selected.id}: {selected.kind} — {selected.description}", file=output)
    print("Probabilities:", file=output)
    for candidate_id, probability in sorted(
        decision.probabilities.items(), key=lambda item: item[1], reverse=True
    ):
        marker = " *" if candidate_id == selected.id else ""
        print(f"  {candidate_id}: {probability:.6f}{marker}", file=output)
    print(f"Confidence: {decision.confidence:.6f}", file=output)
    print(f"Model: {decision.model}", file=output)
    print(f"Latency: {decision.latency_milliseconds} ms", file=output)
    print(f"Request ID: {decision.request_id or 'unavailable'}", file=output)
    print(
        "Usage: "
        f"{decision.input_tokens if decision.input_tokens is not None else 'unknown'} input / "
        f"{decision.output_tokens if decision.output_tokens is not None else 'unknown'} output tokens",
        file=output,
    )
    print("JSON result:", file=output)
    print(json.dumps(result_payload(scenario, decision), indent=2, sort_keys=True), file=output)


def main(
    argv: list[str] | None = None,
    *,
    output: TextIO = sys.stdout,
    error_output: TextIO = sys.stderr,
) -> int:
    """List fixtures or run exactly one live, non-executing Jev request."""

    arguments = build_parser().parse_args(argv)
    # Listing fixtures is offline and intentionally does not load credentials.
    if arguments.list:
        for name in sorted(SCENARIOS):
            print(f"{name}: {SCENARIOS[name].description}", file=output)
        return 0

    try:
        # A fixture run makes exactly one request and never executes desktop actions.
        scenario = get_scenario(arguments.fixture)
        api_key = load_api_key()
        decision = select_action(
            scenario.transcription,
            scenario.desktop_state,
            scenario.candidates,
            api_key=api_key,
            model=arguments.model,
        )
        print_result(scenario, decision, output)
        return 0
    except (ConfigurationError, JevCallError, KeyError) as error:
        print(f"Error: {error}", file=error_output)
        return 1
