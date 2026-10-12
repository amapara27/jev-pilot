"""Prove stable app clauses execute before EOU and final text never replays them."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from threading import Event

from app.cli import main
from app.speech_runtime import StreamingCommands, AudioInputError
from test_cli_e2e import FakeDesktop, FakeJev, FINDER
from test_speech_e2e import FakeSpeechModel


class StreamingCommandEndToEnd(unittest.TestCase):
    """Leave evidence for early dispatch, exact suffixes, corrections and restricted effects."""

    def test_stable_prefix_and_final_suffix(self):
        now = [0.0]
        committed = []
        stream = StreamingCommands({"finder": FINDER.bundle_id}, lambda *item: committed.append(item), clock=lambda: now[0])
        stream.partial("open Finder")
        now[0] = 0.5
        stream.partial("open Finder and search")
        self.assertEqual(committed, [])
        now[0] = 1.0
        stream.partial('open Finder and search for "resume"')
        self.assertEqual(committed, [("open Finder", True, FINDER.bundle_id)])
        stream.final('open Finder and search for "resume" and open it')
        self.assertEqual(committed[-1], ('search for "resume" and open it', False, FINDER.bundle_id))
        self.assertEqual(len(committed), 2)
        # A quoted or unfinished app name and a corrected candidate cannot dispatch.
        for partial in ('open "Finder" and search', "open Finder later", "open Finder actually Safari", "open Finder andromeda"):
            stream.partial(partial)
            now[0] += 1
            stream.partial(partial)
        self.assertEqual(len(committed), 2)
        stream.partial('type "open Finder and write"')
        now[0] = 2.0
        stream.partial('type "open Finder and write"')
        self.assertEqual(len(committed), 2)

        stream.partial("open Finder and search")
        now[0] = 3.0
        stream.partial("open Finder and search more")
        with self.assertRaises(AudioInputError):
            stream.final("open Safari and search")

    def test_prefix_runs_before_final_and_cannot_type(self):
        cases = {}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.json"
            desktop, jev = FakeDesktop(), FakeJev(operation="OPEN_APP", target=FINDER.bundle_id)
            def captured(model, on_final, *, on_partial, **kwargs):
                with patch("app.speech_runtime.monotonic", side_effect=[0.0, 0.6]):
                    on_partial("open Finder and search")
                    on_partial("open Finder and search for resume")
                # Wait for the main-thread worker's native effect before EOU.
                import time
                deadline = time.monotonic() + 2
                while not desktop.effects and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(desktop.effects, 1)
                on_final("open Finder and ")
                return 1
            with patch("app.speech_runtime.run_live_asr", side_effect=captured):
                status = main(["--listen", "--stream-prefixes", "--yes", "--report", str(path)], desktop=desktop, client=jev, speech_model=FakeSpeechModel(), output=io.StringIO())
            report = json.loads(path.read_text())
            self.assertEqual(status, 0, report)
            self.assertEqual(len(report["runs"]), 1)
            self.assertEqual(jev.calls, 1)
            cases["effect_before_eou"] = report
            from app.runtime import run_goal
            blocked = run_goal('type "hi"', FakeDesktop(), client=FakeJev(operation="TYPE_TEXT", target="com.apple.Safari", payload="span_0"), allowed_operations={"OPEN_APP", "FOCUS_APP", "STOP"})
            self.assertFalse(blocked["effect_sent"])
            cases["prefix_typing_blocked"] = blocked
            mismatch = run_goal("open Finder", FakeDesktop(), client=FakeJev(), allowed_operations={"OPEN_APP", "FOCUS_APP", "STOP"}, stream_app=FINDER.bundle_id)
            self.assertFalse(mismatch["effect_sent"])
            cases["wrong_prefix_app"] = mismatch
            drift = run_goal('search for "resume"', FakeDesktop(), client=FakeJev(), intended_bundle=FINDER.bundle_id)
            self.assertFalse(drift["effect_sent"])
            self.assertNotIn("decision", drift)
            cases["dequeue_app_drift"] = drift
            active = FakeDesktop()
            active.active = FINDER
            no_op = run_goal("open Finder", active, client=FakeJev(operation="STOP", target="none"), allowed_operations={"OPEN_APP", "FOCUS_APP", "STOP"}, stream_app=FINDER.bundle_id)
            self.assertEqual(no_op["outcome"], "completed")
            self.assertEqual(active.effects, 0)
            cases["already_active"] = no_op
            from test_cli_e2e import SAFARI
            stale = run_goal("open Finder", active, client=FakeJev(operation="STOP", target="none", after_call=lambda: setattr(active, "active", SAFARI)), allowed_operations={"OPEN_APP", "FOCUS_APP", "STOP"}, stream_app=FINDER.bundle_id)
            self.assertNotEqual(stale["outcome"], "completed")
            self.assertEqual(active.effects, 0)
            cases["already_active_changed_during_jev"] = stale
        artifact = Path(__file__).resolve().parents[1] / ".build/streaming-command-e2e.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text(json.dumps({"evidence": "offline", "scenarios": cases}, indent=2) + "\n")

    def test_overload_cancels_pending_prefix_effect(self):
        """Overflow during Jev retains queued text and withholds its late native decision."""

        requested, overflowed = Event(), Event()
        desktop = FakeDesktop()
        def provider_wait():
            requested.set()
            if not overflowed.wait(2):
                raise AssertionError("Capture did not continue during the Jev request")
        client = FakeJev(operation="OPEN_APP", target=FINDER.bundle_id, after_call=provider_wait)
        def captured(model, on_final, *, on_partial, **kwargs):
            with patch("app.speech_runtime.monotonic", side_effect=[0.0, 0.6]):
                on_partial("open Finder and search")
                on_partial("open Finder and search more")
            if not requested.wait(2):
                raise AssertionError("Prefix never reached Jev")
            try:
                on_final("open Finder and switch to Terminal")
                for _ in range(5):
                    on_final("switch to Terminal")
            finally:
                overflowed.set()
        artifact = Path(__file__).resolve().parents[1] / ".build/streaming-overload-e2e.json"
        with patch("app.speech_runtime.run_live_asr", side_effect=captured):
            status = main(["--listen", "--stream-prefixes", "--report", str(artifact)], desktop=desktop, client=client, speech_model=FakeSpeechModel(), output=io.StringIO())
        report = json.loads(artifact.read_text())
        self.assertEqual(status, 1)
        self.assertEqual(desktop.effects, 0)
        self.assertIn("queue is full", report["error"])
        self.assertEqual(len(report["pending_not_run"]), 5)
        self.assertEqual(len(report["rejected_transcripts"]), 1)
