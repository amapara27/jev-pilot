"""Run the shared observe/Jev/effect/verify loop for accessible desktop workflows."""

from __future__ import annotations

from threading import Event
import re
import sys
from time import perf_counter
from typing import Callable

from .decision import JevCallError, select_action
from .config import ConfigurationError, load_api_key
from .models import ActionCandidate
from .desktop import DesktopError, DesktopState
from .safety import blocked_goal, disposition
from .verification import verify_action, editor_readiness, note_body, same_created_note
from .semantics import SemanticError, SemanticMenu, PREPARATION_OPERATIONS, required_workflow_operations
from .text_input import TextInputError
from .web_navigation import WEB_OPERATIONS
from .interactions import QUOTES, command_actions, action_operation


MAX_STEPS = 4


def run_goal(
    goal: str,
    desktop: object,
    *,
    model: str = "jev-latest",
    dry_run: bool = False,
    approve: Callable[[ActionCandidate], bool] = lambda _: False,
    client: object | None = None,
    cancel_event: Event | None = None,
    allowed_operations: set[str] | None = None,
    intended_bundle: str | None = None,
    stream_app: str | None = None,
    auto_approve_basic: bool = False,
) -> dict[str, object]:
    """Let Jev select each prerequisite; never repeat a sent or uncertain insertion."""

    report = {
        "schema_version": 3, "goal": goal, "outcome": "failed", "python_executable": sys.executable,
        "effect_sent": False, "verified": False,
        "completion_scope": "selected_action", "timings_ms": {}, "steps": [],
    }
    started = stage = perf_counter()
    pending_payload_id = None
    created_note = None
    prepared_field = None
    inserted_field = None
    pressed_controls = set()
    recoveries = 0
    required = required_workflow_operations(goal)
    workflow = bool(required - {"TYPE_TEXT"})
    limit = 8 if workflow else MAX_STEPS
    verified_operations = set()
    commands = command_actions(goal)
    desktop.interaction_commands = commands
    ordered = command_actions(goal, with_positions=True)
    masked = re.sub(QUOTES, lambda m: ' ' * len(m.group()), goal)
    typing = re.search(r'\b(?:type|write|dictate|insert)\s+', masked, re.I)
    if commands and typing and 'TYPE_TEXT' in required:
        ordered.append((typing.start(), 'TYPE_TEXT'))
    ordered = [command for _, command in sorted(ordered)]
    command_index = 0

    def timing(name: str) -> None:
        """Accumulate phase costs across the bounded preparation sequence."""

        nonlocal stage
        now = perf_counter()
        report["timings_ms"][name] = report["timings_ms"].get(name, 0) + round((now - stage) * 1_000)
        stage = now

    def cancellation_requested() -> bool:
        """Stop before another provider request or native effect after voice Stop."""

        if cancel_event is not None and cancel_event.is_set():
            report["outcome"] = "cancelled"
            report["reason"] = "Stopped before the next desktop effect; completed effects remain."
            return True
        return False

    try:
        if not goal.strip():
            raise DesktopError("Enter a nonempty text goal.")
        if blocked_goal(goal):
            report["outcome"] = "blocked"
            report["reason"] = "This request is outside the local safety boundary."
            return report

        for index in range(limit):
            if cancellation_requested():
                return report
            report["verified"] = False
            before: DesktopState = desktop.snapshot()
            if inserted_field is not None and command_index < len(ordered) and ordered[command_index] == 'enter':
                current = next((item for item in before.text_fields if item.id == inserted_field.id), None)
                if not current or not desktop.text.same_target(inserted_field, current, typing=True):
                    raise TextInputError('The inserted text, caret or focus changed before explicit Enter; no submission was sent.')
            if index == 0 and intended_bundle and (before.active is None or before.active.bundle_id != intended_bundle):
                raise DesktopError("The queued instruction's intended app changed; no effect was sent.")
            menu = desktop.semantic_menu(goal, before)
            menu = SemanticMenu(goal, {key: facts for key, facts in menu.targets.items() if key not in pressed_controls}, menu.payloads)
            if prepared_field is not None:
                current = next((item for item in before.text_fields if item.id == prepared_field.id), None)
                if not current or not desktop.text.same_target(prepared_field, current, typing=False):
                    raise DesktopError("The workflow's prepared editor or window changed; no further effect was sent.")
                if "TYPE_TEXT" in required - verified_operations:
                    menu = SemanticMenu(goal, {key: facts for key, facts in menu.targets.items()
                                              if key in {"none", current.id}}, menu.payloads)
            if created_note is not None and "TYPE_TEXT" in required - verified_operations:
                if not same_created_note(desktop, created_note, before):
                    raise DesktopError("The newly created note or its editor changed; no further effect was sent.")
                body = note_body(before)
                menu = SemanticMenu(goal, {key: facts for key, facts in menu.targets.items()
                                          if key in {"none", body.id}}, menu.payloads)
            if ordered:
                next_command = ordered[command_index] if command_index < len(ordered) else None
                menu = SemanticMenu(goal, {key: facts for key, facts in menu.targets.items()
                                          if facts.get('scope') != 'interaction' or facts.get('command') == next_command
                                          and (next_command != 'enter' or inserted_field is None or facts.get('field_id') == inserted_field.id)}, menu.payloads)
            report["observed_app"] = before.active.name if before.active else None
            report["accessibility_trusted"] = before.ax_trusted
            report["editor_readiness"] = editor_readiness(before)
            report["target_ids"], report["payload_ids"] = list(menu.targets), list(menu.payloads)
            timing("observation")
            if required - WEB_OPERATIONS and not before.ax_trusted:
                report["outcome"] = "failed"
                report["reason"] = (
                    "Typing and Notes/Finder workflows require macOS Accessibility permission for this Python host. "
                    "Run python -m app --request-accessibility from the same terminal, enable the identified host "
                    "in System Settings > Privacy & Security > Accessibility, and restart it if needed. "
                    "No Jev request or desktop effect was sent."
                )
                return report
            state = before.provider_state()
            # Prior verified operations help Jev advance rather than repeat preparation.
            state["verified_steps"] = [item["decision"]["semantic"]["operation"] for item in report["steps"] if item["verified"]]
            state["pending_payload_id"] = pending_payload_id
            state["required_operations"] = sorted(required) if workflow else []
            state["remaining_operations"] = sorted(required - verified_operations) if workflow else []
            if ordered:
                state['next_command'] = next_command
                state['remaining_operations'] = sorted((required - verified_operations) | {
                    'TYPE_TEXT' if item == 'TYPE_TEXT' else action_operation(item) for item in ordered[command_index:]})
            state["recent_actions"] = [{"operation": item["decision"]["semantic"]["operation"],
                                        "target_id": item["decision"]["semantic"]["target_id"],
                                        "effect_sent": item["effect_sent"], "verified": item["verified"],
                                        "outcome": item.get("outcome")}
                                       for item in report["steps"][-8:]]
            decision = select_action(
                goal, state, menu, api_key=load_api_key() if client is None else None,
                model=model, client=client,
            )
            action = decision.selected_candidate
            report["decision"] = decision.to_dict()
            step = {"index": index + 1, "decision": decision.to_dict(), "effect_sent": False, "verified": False}
            report["steps"].append(step)
            timing("jev")
            if cancellation_requested():
                return report
            if allowed_operations is not None and decision.semantic["operation"] not in allowed_operations:
                report["outcome"] = "unsupported"
                report["reason"] = "Streaming app clauses may only open or focus an app; this effect was withheld."
                return report
            if stream_app and action is not None and action.parameters.get("bundle_identifier") != stream_app:
                raise DesktopError("The streaming decision did not match the explicitly named app; no effect was sent.")
            payload_id = decision.semantic["payload_id"]
            if pending_payload_id is not None and payload_id not in {"none", pending_payload_id}:
                raise SemanticError("Jev changed the prepared typing payload; no further effect was sent.")
            if decision.semantic["operation"] == "STOP":
                if stream_app and before.active and before.active.bundle_id == stream_app:
                    current = desktop.snapshot()
                    if current.active != before.active:
                        raise DesktopError("The already-active app changed during Jev; the streaming clause was not verified.")
                    step.update(verified=True, outcome="already_active")
                    report.update(outcome="completed", verified=True, completion_scope="already_active")
                    return report
                if required == {"PRESS_CONTROL"} and pressed_controls:
                    report.update(outcome="completed", verified=True, completion_scope="verified_actions",
                                  reason="Jev stopped after observable control effects; arbitrary goal completion is not proven.")
                    return report
                report["outcome"] = "incomplete" if index else "stopped"
                report["reason"] = "Jev selected STOP before the requested effects were verified; no further effect was sent."
                return report
            if action is None:
                report["outcome"] = "unsupported"
                report["reason"] = "No executable grounded target is available for the selected operation."
                return report
            if ordered and action.kind in {'TYPE_TEXT', 'KEYBOARD_ACTION', 'SCROLL'}:
                selected = 'TYPE_TEXT' if action.kind == 'TYPE_TEXT' else action.parameters['command']
                if selected != next_command:
                    raise DesktopError('The selected input action skipped the next explicit command; no effect was sent.')
            if action.kind == 'TYPE_TEXT' and 'TYPE_TEXT' in verified_operations:
                raise DesktopError('The insertion already verified; it was not repeated.')
            if ordered and action.kind == 'PRESS_CONTROL' and 'PRESS_CONTROL' not in required:
                raise DesktopError('Explicit keyboard commands must use their grounded command target; no generic press was sent.')
            if action.kind == 'TYPE_TEXT' and commands:
                payload = decision.semantic['payload']
                tails = [position for position, _ in command_actions(goal, with_positions=True) if position > typing.start()]
                if tails and payload['end'] > min(tails):
                    raise SemanticError('The chosen typing span includes an explicit command tail; quote it to type those words literally.')
            if action.parameters.get("continue_typing") and not before.ax_trusted:
                raise TextInputError("Typing preparation requires macOS Accessibility permission.")
            if (action.kind == "TYPE_TEXT" and bool((required & {"CREATE_NOTE", "PRESS_CONTROL"}) - verified_operations)
                    or action.kind in {"SELECT_ITEM", "OPEN_ITEM"} and "SEARCH" in required and "SEARCH" not in verified_operations):
                report["outcome"] = "incomplete"
                report["reason"] = "Jev skipped a required creation/search prerequisite; no effect was sent."
                return report
            if action.kind in {"CREATE_NOTE", "SEARCH", "SELECT_ITEM", "OPEN_ITEM"} and action.kind in verified_operations:
                raise DesktopError("This workflow effect already verified; it was not repeated.")
            if action.kind == "PRESS_CONTROL" and action.parameters["element_id"] in pressed_controls:
                raise DesktopError("This control effect already verified; it was not repeated.")
            if prepared_field is not None and action.kind in {"TYPE_TEXT", "FOCUS_FIELD"} and action.parameters["element_id"] != prepared_field.id:
                raise DesktopError("Jev selected a different editor from the verified preparation; no effect was sent.")
            if decision.semantic["operation"] in PREPARATION_OPERATIONS and payload_id != "none":
                pending_payload_id = payload_id
            if created_note is not None and action.kind == "TYPE_TEXT":
                body = note_body(before)
                if action.parameters["element_id"] != body.id or body.value != "" or body.selection != (0, 0):
                    raise DesktopError("The new note is not an empty body with a readable starting caret; no text was sent.")

            def validate_target() -> DesktopState:
                """Bind decision and approval to the same live application/editor facts."""

                fresh = desktop.snapshot()
                if prepared_field is not None:
                    current = next((item for item in fresh.text_fields if item.id == prepared_field.id), None)
                    if not current or not desktop.text.same_target(prepared_field, current, typing=False):
                        raise DesktopError("The workflow's prepared editor changed while deciding or approving; no effect was sent.")
                if created_note is not None and not same_created_note(desktop, created_note, fresh):
                    raise DesktopError("The newly created note changed while deciding or approving; no effect was sent.")
                desktop.validate_action(action, before, fresh)
                target_id = decision.semantic["target_id"]
                if desktop.semantic_menu(goal, fresh).targets.get(target_id) != decision.semantic["target"]:
                    raise DesktopError("The selected target is no longer a current action.")
                return fresh

            try:
                fresh = validate_target()
            except DesktopError:
                fresh = desktop.snapshot()
                original = next((item for item in before.workflow_targets if item.id == action.parameters.get("element_id")), None)
                replacement = next((item for item in fresh.workflow_targets if original and item.kind == original.kind
                                    and item.label == original.label and item.pid == original.pid
                                    and desktop.text.equal(item.window, original.window)
                                    and not desktop.text.equal(item.element, original.element)
                                    and item.selected == original.selected and item.expanded == original.expanded), None)
                if action.kind not in {"CREATE_NOTE", "PREPARE_SEARCH", "PRESS_CONTROL"} or before.active != fresh.active or replacement is None or recoveries >= 2:
                    raise
                recoveries += 1
                step.update(outcome="reobserve", reason="The control changed before dispatch; asking Jev against fresh targets.")
                timing("recovery")
                continue
            timing("fresh_target")
            if cancellation_requested():
                return report
            policy = disposition(action, decision.confidence, auto_approve_basic=auto_approve_basic)
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

            if cancellation_requested():
                return report

            # Dispatch failure can still mean a partial effect. Never replay it.
            report["effect_sent"] = step["effect_sent"] = True
            report["verified"] = False
            desktop.execute(action)
            timing("execution")
            observations, diagnostics = [], {}
            verified = verify_action(desktop, action, fresh, observations=observations, diagnostics=diagnostics, cancel_event=cancel_event)
            step["verification"] = diagnostics
            report["verified"] = step["verified"] = verified
            timing("verification")
            if cancellation_requested():
                return report
            if not verified:
                report["outcome"] = "unverified"
                report["reason"] = "The exact intended effect was not observed; it was not retried."
                if action.kind in {"OPEN_APP", "FOCUS_APP"} and (action.parameters.get("continue_typing") or action.parameters.get("continue_workflow")):
                    report["reason"] = "App preparation did not reach a usable focused Accessibility window; no further Jev decision or typing was sent."
                elif diagnostics.get("reason"):
                    report["reason"] = f"{action.kind} could not be verified ({diagnostics['reason']}); it was not retried."
                return report
            if action.kind == "CREATE_NOTE":
                created_note = observations[-1]
            if action.kind == "PRESS_CONTROL":
                pressed_controls.add(action.parameters["element_id"])
                if diagnostics["reason"] == "editor_changed" and len(observations[-1].text_fields) == 1:
                    prepared_field = observations[-1].text_fields[0]
            verified_operations.add(action.kind)
            if action.kind == 'TYPE_TEXT' and commands:
                inserted_field = next(item for item in observations[-1].text_fields if item.id == action.parameters['element_id'])
            if ordered and action.kind in {'TYPE_TEXT', 'KEYBOARD_ACTION', 'SCROLL'}:
                command_index += 1
            if workflow:
                if required <= verified_operations and command_index == len(ordered) and required != {"PRESS_CONTROL"}:
                    report["outcome"] = "completed"
                    report["completion_scope"] = "verified_workflow"
                    report["verified_operations"] = sorted(verified_operations)
                    return report
                if action.kind == "TYPE_TEXT" and not commands:
                    report["outcome"] = "incomplete"
                    report["reason"] = "Typing verified but another requested workflow effect remains; insertion was not repeated."
                    return report
                continue
            if action.kind in {"FOCUS_FIELD", "PRESS_CONTROL"} or action.parameters.get("continue_typing") or action.parameters.get("continue_workflow") or action.kind == "PREPARE_SEARCH":
                continue
            report["outcome"] = "completed"
            if action.kind == "TYPE_TEXT":
                report["completion_scope"] = "single_insertion"
            return report
        report["outcome"] = "incomplete"
        report["reason"] = f"The {limit}-step preparation/workflow limit was reached; completed effects remain."
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
