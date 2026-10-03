"""Expose the Python text-to-native-action milestone through a small CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import TextIO

import ApplicationServices as AX
from .models import ActionCandidate

from .desktop import MacDesktop
from .runtime import run_goal


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ask Jev for one grounded macOS app action.")
    parser.add_argument("goal", nargs="*", help="Text goal, such as 'switch to Finder'.")
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument("--dry-run", action="store_true", help="Choose without executing.")
    parser.add_argument("--yes", action="store_true", help="Approve low-confidence app actions.")
    parser.add_argument("--request-accessibility", action="store_true", help="Ask macOS for Accessibility access for this CLI host.")
    parser.add_argument("--report", type=Path, default=Path(".build/python-cli-last.json"))
    return parser


def _save_report(path: Path, report: dict[str, object]) -> None:
    """Write one compact artifact atomically, including failure outcomes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")
    os.replace(temporary, path)


def main(
    argv: list[str] | None = None,
    *,
    desktop: object | None = None,
    client: object | None = None,
    output: TextIO = sys.stdout,
) -> int:
    """Run one text goal; Ctrl-C reports cancellation without retrying sent effects."""

    args = _parser().parse_args(argv)
    if args.request_accessibility:
        trusted = bool(AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True}))
        print(json.dumps({"python_executable": sys.executable, "accessibility_trusted": trusted}, indent=2), file=output)
        return 0 if trusted else 1
    if not args.goal:
        _parser().error("a text goal is required unless --request-accessibility is used")
    goal = " ".join(args.goal)

    def approve(action: ActionCandidate) -> bool:
        if args.yes:
            return True
        if not sys.stdin.isatty():
            return False
        return input(f"Approve {action.description} [y/N] ").strip().casefold() == "y"

    try:
        report = run_goal(
            goal,
            desktop if desktop is not None else MacDesktop(),
            model=args.model,
            dry_run=args.dry_run,
            approve=approve,
            client=client,
        )
    except KeyboardInterrupt:
        report = {"schema_version": 1, "goal": goal, "outcome": "cancelled", "reason": "Interrupted; an already sent effect cannot be undone."}
    _save_report(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True), file=output)
    return 0 if report["outcome"] in {"completed", "dry_run"} else 1
