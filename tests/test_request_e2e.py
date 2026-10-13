"""Reproduce expanded-catalog request growth and safe API failure reporting through the CLI."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import httpx2
from typesafe_sdk import TypeSafeBadRequestError
from typesafe_sdk._core.json import serialize

from app.cli import main
from app.desktop import AppState, DesktopState
from app.workflows import WorkflowTarget
from test_cli_e2e import FakeDesktop, FakeJev, SAFARI, TERMINAL


class CatalogDesktop(FakeDesktop):
    """Expose hundreds of installed apps without reading or modifying the real desktop."""

    def __init__(self, count=263, *, controls=0):
        super().__init__()
        self.applications = tuple((f"Synthetic installed application {index:03}", f"com.example.application{index:03}",
                                  (f"synthetic application {index:03}",)) for index in range(count - 4))
        self.workspace.installed.update(item[1] for item in self.applications)
        self.workspace.installed.add("com.apple.Notes")
        self.controls = tuple(WorkflowTarget(f'workflow_{index}', 'control', 100, SAFARI.bundle_id,
                             f'Media library menu control {index:03} ' + 'x' * 200, object(), object(), role='AXMenuItem')
                              for index in range(controls))

    def snapshot(self):
        state = super().snapshot()
        running = (SAFARI, TERMINAL) + tuple(AppState(name, bundle, 1000 + index)
                    for index, (name, bundle, _) in enumerate(self.applications[:39]))
        return DesktopState(state.active, running, True, True, workflow_targets=self.controls)

    def execute(self, action):
        self.effects += 1
        self.active = AppState("Notes", "com.apple.Notes", 999)


class RequestClient(FakeJev):
    """Measure actual SDK serialization and simulate a server token-budget rejection."""

    def __init__(self, *, always_reject=False):
        super().__init__(operation="OPEN_APP", target="com.apple.Notes")
        self.request_bytes = 0
        self.always_reject = always_reject

    def system_one(self, state, questions, *, model=None):
        self.state_bytes = len(serialize(state))
        self.shared_target_count = len(state['semantic_options']['targets'])
        self.compatibility_duplicated = any('available_targets' in facts for facts in state['semantic_options']['operations'].values())
        self.request_bytes = len(serialize({"model": model, "state": state, "questions": questions}))
        if self.always_reject or self.request_bytes > 128000:
            self.calls += 1
            raise TypeSafeBadRequestError(400, {"detail": {"error_type": "max_tokens_exceeded", "input": "PRIVATE_TEST_CONTENT"}},
                                          httpx2.Headers({"x-typesafe-request-id": "offline-request-limit"}))
        return super().system_one(state, questions, model=model)


class RequestEndToEnd(unittest.TestCase):
    """Keep the catalog intact while proving bounded requests and no effects on failure."""

    @unittest.skipUnless(os.environ.get("JEV_LIVE_JEV") == "1", "Live Jev is opt-in; desktop services stay synthetic.")
    def test_live_catalog_request(self):
        """Verify the real provider accepts the expanded catalog without a desktop effect."""

        desktop = CatalogDesktop(controls=36)
        artifact = Path(__file__).resolve().parents[1] / ".build/live-request-e2e.json"
        status = main(["Open notes.", "--dry-run", "--report", str(artifact)], desktop=desktop, output=io.StringIO())
        report = json.loads(artifact.read_text())
        self.assertEqual(status, 0, report.get("reason"))
        self.assertEqual(report["outcome"], "dry_run", report.get("reason"))
        self.assertEqual(report["decision"]["selected_candidate"]["parameters"]["bundle_identifier"], "com.apple.Notes")
        self.assertEqual(desktop.effects, 0)

    def test_dense_foreground_context_keeps_one_compatibility_copy(self):
        desktop, client = CatalogDesktop(controls=36), RequestClient()
        artifact = Path('.build/dense-request-e2e.json')
        status = main(['Open notes.', '--dry-run', '--report', str(artifact)], desktop=desktop, client=client, output=io.StringIO())
        report = json.loads(artifact.read_text())
        self.assertEqual(status, 0, report.get('reason'))
        self.assertFalse(client.compatibility_duplicated)
        self.assertLess(client.state_bytes, 80000)
        self.assertLess(client.request_bytes, 115000)
        self.assertIn('com.example.application258', report['target_ids'])
        self.assertEqual(desktop.effects, 0)
        artifact.write_text(json.dumps({'report': report, 'request_bytes': client.request_bytes, 'state_bytes': client.state_bytes}, indent=2))

    @unittest.skipUnless(os.environ.get('JEV_LIVE_JEV') == '1', 'Live Jev opt-in; no real desktop effects.')
    def test_live_suffix_search_request(self):
        """Replay the reported phrase with hundreds of apps and a dense foreground menu."""

        desktop = CatalogDesktop(controls=36)
        artifact = Path('.build/live-search-request-e2e.json')
        status = main(['search for messi soccer videos on youtube', '--dry-run', '--report', str(artifact)],
                      desktop=desktop, output=io.StringIO())
        report = json.loads(artifact.read_text())
        self.assertEqual(status, 0, report.get('reason'))
        self.assertEqual(report['decision']['semantic']['operation'], 'SEARCH_WEB')
        self.assertEqual(report['decision']['selected_candidate']['parameters']['url'],
                         'https://www.youtube.com/results?search_query=messi+soccer+videos')
        self.assertEqual(desktop.effects, 0)

    def test_catalog_size_and_provider_failure(self):
        cases = {}
        for name, desktop, client, expected in (
            ("expanded_catalog", CatalogDesktop(), RequestClient(), "completed"),
            ("safe_context_error", CatalogDesktop(), RequestClient(always_reject=True), "failed"),
            ("choice_limit", CatalogDesktop(340), RequestClient(), "failed"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                goal = 'Bring up a useful app' if name == 'choice_limit' else 'Open notes.'
                main([goal, "--report", str(path)], desktop=desktop, client=client, output=io.StringIO())
                report = json.loads(path.read_text())
            cases[name] = {"request_bytes": client.request_bytes, "provider_calls": client.calls, "report": report}
            self.assertEqual(report["outcome"], expected, report.get("reason"))
            self.assertEqual(desktop.effects, int(expected == "completed"))
            if name == "expanded_catalog":
                self.assertLess(client.request_bytes, 128000)
                self.assertEqual(client.calls, 1)
                self.assertIn("com.example.application258", report["target_ids"])
            elif name == "safe_context_error":
                self.assertIn("context", report["reason"])
                self.assertNotIn("PRIVATE_TEST_CONTENT", json.dumps(report))
                self.assertIn("offline-request-limit", report["reason"])
            else:
                self.assertEqual(client.calls, 0)
                self.assertIn("255", report["reason"])
        artifact = Path(__file__).resolve().parents[1] / ".build/request-e2e.json"
        artifact.write_text(json.dumps({"evidence": "offline SDK serialization and native/provider boundaries", "scenarios": cases}, indent=2) + "\n")
