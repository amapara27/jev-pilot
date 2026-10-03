"""Connect one text goal to observation, Jev, local safety, effect, and verification."""

from __future__ import annotations

from time import perf_counter
from typing import Callable

from .decision import JevCallError, select_action
from .config import ConfigurationError, load_api_key
from .models import ActionCandidate

from .desktop import DesktopError, DesktopState
from .safety import blocked_goal, disposition
from .verification import verify_app


def run_goal(
    goal: str,
    desktop: object,
    *,
    model: str = "jev-latest",
    dry_run: bool = False,
    approve: Callable[[ActionCandidate], bool] = lambda _: False,
    client: object | None = None,
) -> dict[str, object]:
    """Run one bounded decision and return a compact, serializable audit record."""

    report: dict[str, object] = {
        "schema_version": 1,
        "goal": goal.strip(),
        "outcome": "failed",
        "effect_sent": False,
        "verified": False,
        "timings_ms": {},
    }
    started = perf_counter()
    stage = started

    def timing(name: str) -> None:
        nonlocal stage
        now = perf_counter()
        report["timings_ms"][name] = round((now - stage) * 1_000)
        stage = now

    try:
        if not report["goal"]:
            raise DesktopError("Enter a nonempty text goal.")
        if blocked_goal(goal):
            report["outcome"] = "blocked"
            report["reason"] = "This request is outside the first milestone's safety boundary."
            return report

        before: DesktopState = desktop.snapshot()
        candidates = desktop.candidates(goal, before)
        report["observed_app"] = before.active.name if before.active else None
        report["accessibility_trusted"] = before.ax_trusted
        report["candidates"] = [{"id": item.id, "kind": item.kind} for item in candidates]
        timing("observation")
        if len(candidates) == 1 and candidates[0].kind == "STOP":
            report["outcome"] = "unsupported"
            report["reason"] = "No supported named-app action is available for this goal."
            return report

        decision = select_action(
            goal,
            before.provider_state(),
            candidates,
            api_key=load_api_key() if client is None else None,
            model=model,
            client=client,
        )
        action = decision.selected_candidate
        report["decision"] = {
            "id": action.id,
            "kind": action.kind,
            "confidence": decision.confidence,
            "probabilities": decision.probabilities,
            "model": decision.model,
            "latency_ms": decision.latency_milliseconds,
        }
        timing("jev")
        if action.kind == "STOP":
            report["outcome"] = "stopped"
            report["reason"] = "Jev selected STOP; no desktop effect was sent."
            return report

        fresh: DesktopState = desktop.snapshot()
        if not before.active or not fresh.active or fresh.active.pid != before.active.pid:
            raise DesktopError("The foreground app changed while Jev was deciding.")
        if action not in desktop.candidates(goal, fresh):
            raise DesktopError("The selected application is no longer a current action.")
        timing("fresh_target")

        policy = disposition(action, decision.confidence)
        report["safety"] = policy
        if policy == "deny":
            report["outcome"] = "blocked"
            return report
        if dry_run:
            report["outcome"] = "dry_run"
            return report
        if policy == "confirm" and not approve(action):
            report["outcome"] = "rejected"
            return report

        # Once dispatch begins, a timeout may still mean the OS received the effect.
        report["effect_sent"] = True
        desktop.execute(action)
        timing("execution")
        report["verified"] = verify_app(desktop, action)
        timing("verification")
        report["outcome"] = "completed" if report["verified"] else "unverified"
        if not report["verified"]:
            report["reason"] = "The app did not become frontmost; the effect was not retried."
    except (DesktopError, JevCallError, ConfigurationError) as error:
        report["reason"] = str(error)
    except KeyboardInterrupt:
        report["outcome"] = "cancelled"
        report["reason"] = "Interrupted; any already sent effect cannot be undone."
    except Exception as error:
        report["reason"] = f"Native or provider integration failed ({type(error).__name__})."
    finally:
        report["timings_ms"]["total"] = round((perf_counter() - started) * 1_000)
    return report
