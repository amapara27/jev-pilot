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
from .semantics import SemanticError


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
        "schema_version": 2,
        "goal": goal,
        "outcome": "failed",
        "effect_sent": False,
        "verified": False,
        "completion_scope": "selected_action",
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
        if not goal.strip():
            raise DesktopError("Enter a nonempty text goal.")
        if blocked_goal(goal):
            report["outcome"] = "blocked"
            report["reason"] = "This request is outside the local safety boundary."
            return report

        before: DesktopState = desktop.snapshot()
        menu = desktop.semantic_menu(goal, before)
        report["observed_app"] = before.active.name if before.active else None
        report["accessibility_trusted"] = before.ax_trusted
        report["target_ids"] = list(menu.targets)
        report["payload_ids"] = list(menu.payloads)
        timing("observation")

        decision = select_action(
            goal,
            before.provider_state(),
            menu,
            api_key=load_api_key() if client is None else None,
            model=model,
            client=client,
        )
        action = decision.selected_candidate
        report["decision"] = decision.to_dict()
        timing("jev")
        if decision.semantic["operation"] == "STOP":
            report["outcome"] = "stopped"
            report["reason"] = "Jev selected STOP; no desktop effect was sent."
            return report
        if action is None:
            report["outcome"] = "unsupported"
            report["reason"] = "The selected operation has no native executor yet; no effect was sent."
            return report

        def validate_target() -> None:
            """Bind both decision and approval to the same live app facts."""

            fresh: DesktopState = desktop.snapshot()
            if fresh.active != before.active or fresh.active is None:
                raise DesktopError("The foreground app changed while deciding or approving.")
            target_id = decision.semantic["target_id"]
            fresh_target = desktop.semantic_menu(goal, fresh).targets.get(target_id)
            if fresh_target != decision.semantic["target"]:
                raise DesktopError("The selected application is no longer a current action.")

        validate_target()
        timing("fresh_target")

        policy = disposition(action, decision.confidence)
        report["safety"] = policy
        if policy == "deny":
            report["outcome"] = "blocked"
            return report
        if dry_run:
            report["outcome"] = "dry_run"
            return report
        if policy == "confirm":
            if not approve(action):
                report["outcome"] = "rejected"
                return report
            validate_target()
            timing("approval")

        # Once dispatch begins, a timeout may still mean the OS received the effect.
        report["effect_sent"] = True
        desktop.execute(action)
        timing("execution")
        report["verified"] = verify_app(desktop, action)
        timing("verification")
        report["outcome"] = "completed" if report["verified"] else "unverified"
        if not report["verified"]:
            report["reason"] = "The app did not become frontmost; the effect was not retried."
    except (DesktopError, JevCallError, ConfigurationError, SemanticError) as error:
        report["reason"] = str(error)
    except KeyboardInterrupt:
        report["outcome"] = "cancelled"
        report["reason"] = "Interrupted; any already sent effect cannot be undone."
    except Exception as error:
        report["reason"] = f"Native or provider integration failed ({type(error).__name__})."
    finally:
        report["timings_ms"]["total"] = round((perf_counter() - started) * 1_000)
    return report
