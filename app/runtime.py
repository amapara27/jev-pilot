"""Connect semantic choices to bounded app/field preparation and one verified insertion."""

from __future__ import annotations

from time import perf_counter
from typing import Callable

from .decision import JevCallError, select_action
from .config import ConfigurationError, load_api_key
from .models import ActionCandidate
from .desktop import DesktopError, DesktopState
from .safety import blocked_goal, disposition
from .verification import verify_action
from .semantics import SemanticError, SemanticMenu
from .text_input import TextInputError


MAX_STEPS = 4


def run_goal(
    goal: str,
    desktop: object,
    *,
    model: str = "jev-latest",
    dry_run: bool = False,
    approve: Callable[[ActionCandidate], bool] = lambda _: False,
    client: object | None = None,
) -> dict[str, object]:
    """Let Jev select each prerequisite; never repeat a sent or uncertain insertion."""

    report = {
        "schema_version": 3, "goal": goal, "outcome": "failed",
        "effect_sent": False, "verified": False,
        "completion_scope": "selected_action", "timings_ms": {}, "steps": [],
    }
    started = stage = perf_counter()
    pending_payload_id = None
    field_choice_bundle = None

    def timing(name: str) -> None:
        """Accumulate phase costs across the bounded preparation sequence."""

        nonlocal stage
        now = perf_counter()
        report["timings_ms"][name] = report["timings_ms"].get(name, 0) + round((now - stage) * 1_000)
        stage = now

    try:
        if not goal.strip():
            raise DesktopError("Enter a nonempty text goal.")
        if blocked_goal(goal):
            report["outcome"] = "blocked"
            report["reason"] = "This request is outside the local safety boundary."
            return report

        for index in range(MAX_STEPS):
            report["verified"] = False
            before: DesktopState = desktop.snapshot()
            menu = desktop.semantic_menu(goal, before)
            if field_choice_bundle is not None:
                if before.active is None or before.active.bundle_id != field_choice_bundle:
                    raise DesktopError("The foreground app changed before choosing its typing field.")
                # Correct an app-level typing choice through Jev, using only
                # this app's fresh fields. No field is selected locally.
                menu = SemanticMenu(goal, {
                    key: facts for key, facts in menu.targets.items()
                    if key == "none" or (facts.get("scope") == "field"
                                         and facts.get("bundle_identifier") == field_choice_bundle)
                }, menu.payloads)
            report["observed_app"] = before.active.name if before.active else None
            report["accessibility_trusted"] = before.ax_trusted
            report["editor_readiness"] = {
                "focused_window_available": before.focused_window_available,
                "field_count": len(before.text_fields),
                "can_type_count": sum(item.can_type for item in before.text_fields),
                **before.editor_diagnostics,
            }
            report["target_ids"], report["payload_ids"] = list(menu.targets), list(menu.payloads)
            timing("observation")
            state = before.provider_state()
            # Prior verified operations help Jev advance rather than repeat preparation.
            state["verified_steps"] = [item["decision"]["semantic"]["operation"] for item in report["steps"] if item["verified"]]
            state["pending_payload_id"] = pending_payload_id
            decision = select_action(
                goal, state, menu, api_key=load_api_key() if client is None else None,
                model=model, client=client,
            )
            action = decision.selected_candidate
            report["decision"] = decision.to_dict()
            step = {"index": index + 1, "decision": decision.to_dict(), "effect_sent": False, "verified": False}
            report["steps"].append(step)
            timing("jev")
            payload_id = decision.semantic["payload_id"]
            if pending_payload_id is not None and payload_id not in {"none", pending_payload_id}:
                raise SemanticError("Jev changed the prepared typing payload; no further effect was sent.")
            if decision.semantic["operation"] == "STOP":
                report["outcome"] = "incomplete" if index else "stopped"
                report["reason"] = "Jev selected STOP before typing was completed; no further effect was sent."
                return report
            if action is None:
                target = decision.semantic["target"]
                if (decision.semantic["operation"] == "TYPE_TEXT"
                        and target.get("scope") == "application"
                        and field_choice_bundle is None and before.ax_trusted
                        and before.active and before.active.bundle_id == decision.semantic["target_id"]
                        and any(item.bundle_id == before.active.bundle_id
                                and (item.can_type or (item.focusable and not item.focused))
                                for item in before.text_fields)):
                    field_choice_bundle = before.active.bundle_id
                    pending_payload_id = payload_id
                    step["outcome"] = "reselect_field"
                    step["reason"] = "Jev selected the app for typing; requesting a fresh observed field choice."
                    continue
                report["outcome"] = "unsupported"
                report["reason"] = "No executable grounded target is available for the selected operation."
                if decision.semantic["operation"] == "TYPE_TEXT":
                    if not before.ax_trusted:
                        report["reason"] = "Typing requires macOS Accessibility permission for this Python host."
                    elif not before.text_fields:
                        name = before.active.name if before.active else "the active app"
                        report["reason"] = (
                            f"No editable field was discovered in {name}; TYPE_TEXT needs a field target. "
                            "Open an existing editable document and focus its body. "
                            "See editor_readiness for discovery failures; document creation is not implemented."
                        )
                    else:
                        report["reason"] = "TYPE_TEXT selected an application target; insertion requires an observed field target."
                return report
            if action.parameters.get("continue_typing") and not before.ax_trusted:
                raise TextInputError("Typing preparation requires macOS Accessibility permission.")
            if index and action.kind in {"OPEN_APP", "FOCUS_APP"} and not action.parameters.get("continue_typing"):
                raise DesktopError("A typing preparation sequence cannot finish with an ordinary app activation.")
            if (action.kind == "FOCUS_FIELD" or action.parameters.get("continue_typing")) and payload_id != "none":
                pending_payload_id = payload_id

            def validate_target() -> DesktopState:
                """Bind decision and approval to the same live application/editor facts."""

                fresh = desktop.snapshot()
                desktop.validate_action(action, before, fresh)
                target_id = decision.semantic["target_id"]
                if desktop.semantic_menu(goal, fresh).targets.get(target_id) != decision.semantic["target"]:
                    raise DesktopError("The selected target is no longer a current action.")
                return fresh

            fresh = validate_target()
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
                fresh = validate_target()
                timing("approval")

            # Dispatch failure can still mean a partial effect. Never replay it.
            report["effect_sent"] = step["effect_sent"] = True
            report["verified"] = False
            desktop.execute(action)
            timing("execution")
            verified = verify_action(desktop, action, fresh)
            report["verified"] = step["verified"] = verified
            timing("verification")
            if not verified:
                report["outcome"] = "unverified"
                report["reason"] = "The exact intended effect was not observed; it was not retried."
                return report
            if action.kind == "FOCUS_FIELD" or action.parameters.get("continue_typing"):
                continue
            report["outcome"] = "completed"
            if action.kind == "TYPE_TEXT":
                report["completion_scope"] = "single_insertion"
            return report
        report["outcome"] = "incomplete"
        report["reason"] = "The four-step typing preparation limit was reached; completed effects remain."
    except (DesktopError, JevCallError, ConfigurationError, SemanticError, TextInputError) as error:
        if isinstance(error, JevCallError) and error.rejected_decision is not None:
            report["rejected_decision"] = error.rejected_decision
        if isinstance(error, TextInputError) and report["steps"]:
            report["steps"][-1]["effect_sent"] = error.effect_sent
            report["effect_sent"] = any(item["effect_sent"] for item in report["steps"])
        report["reason"] = str(error)
    except KeyboardInterrupt:
        report["outcome"] = "cancelled"
        report["reason"] = "Interrupted; any already sent effect cannot be undone."
    except Exception as error:
        report["reason"] = f"Native or provider integration failed ({type(error).__name__})."
    finally:
        report["timings_ms"]["total"] = round((perf_counter() - started) * 1_000)
    return report
