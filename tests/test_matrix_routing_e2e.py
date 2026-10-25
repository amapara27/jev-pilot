"""Check offline that every native-matrix command still routes to its intended operation and approval."""

import json
from pathlib import Path
import unittest

from app.interactions import InteractionTarget, command_actions
from app.models import ActionCandidate
from app.safety import blocked_goal, disposition, requires_confirmation
from app.semantics import required_workflow_operations
from scripts.native_matrix import cases


# Matrix cases that aren't keyboard/scroll commands route through these workflow operations instead.
OPERATIONS = {"type_text": {"TYPE_TEXT"}, "open_app": set(), "open_website": {"OPEN_WEBSITE"},
              "google_search": {"SEARCH_WEB"}, "finder_search": {"SEARCH"}}
MANDATORY_APPROVAL = {"cut", "close_tab"}


def interaction(command, bundle="com.apple.Safari"):
    """Resolve a grounded interaction target the way semantics.py hands it to safety."""

    facts = InteractionTarget(id="interaction_0", command=command, pid=1, bundle=bundle,
                              element=None, window=None, focused=None).provider_target()
    kind = "SCROLL" if command.startswith("scroll_") else "KEYBOARD_ACTION"
    return ActionCandidate("interaction_0", kind, {"bundle_identifier": bundle, "command": command,
                                                   "requires_confirmation": facts["requires_confirmation"]}, command)


class MatrixRoutingEndToEnd(unittest.TestCase):
    def test_every_matrix_goal_routes_to_its_command(self):
        records = []
        for case in cases():
            with self.subTest(case.name):
                commands = command_actions(case.goal)
                required = required_workflow_operations(case.goal)
                self.assertFalse(blocked_goal(case.goal))
                if case.name in OPERATIONS:
                    self.assertEqual(required, OPERATIONS[case.name])
                    self.assertEqual(commands, [])
                else:
                    self.assertEqual(commands, [case.expects])
                    self.assertEqual(required, {"SCROLL" if case.expects.startswith("scroll_") else "KEYBOARD_ACTION"})
                records.append({"case": case.name, "goal": case.goal, "commands": commands, "required": sorted(required)})
        Path(".build").mkdir(exist_ok=True)
        Path(".build/matrix-routing-e2e.json").write_text(json.dumps(records, indent=2))

    def test_risky_commands_always_ask_and_terminal_input_is_denied(self):
        for case in cases():
            if case.name in OPERATIONS:
                continue
            with self.subTest(case.name):
                action = interaction(case.expects)
                mandatory = case.expects in MANDATORY_APPROVAL
                self.assertEqual(requires_confirmation(action), mandatory)
                # Even full confidence cannot skip a mandatory approval.
                self.assertEqual(disposition(action, 1.0), "confirm" if mandatory else "allow")
                self.assertEqual(disposition(action, 0.5), "confirm")
        for command in ("paste", "undo", "copy"):
            self.assertEqual(disposition(interaction(command, "com.apple.Terminal"), 1.0), "deny")


if __name__ == "__main__":
    unittest.main()
