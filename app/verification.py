"""Verify the selected application actually reaches the foreground."""

from __future__ import annotations

from time import monotonic, sleep

from .models import ActionCandidate


def verify_app(desktop: object, action: ActionCandidate, timeout_seconds: float = 3) -> bool:
    """Poll the native foreground identity; never replay an uncertain effect."""

    expected = action.parameters["bundle_identifier"]
    deadline = monotonic() + timeout_seconds
    while True:
        state = desktop.snapshot()
        if state.active and state.active.bundle_id == expected:
            return True
        if monotonic() >= deadline:
            return False
        sleep(0.05)
