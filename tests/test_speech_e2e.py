"""Prove captured audio reaches Jev and a verified desktop effect, without MLX or live I/O."""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from threading import Event

import numpy as np

from app.cli import main
from app.runtime import run_goal
from test_cli_e2e import FakeDesktop, FakeJev


class FakeStreamingSession:
    """Stand in for the model session while keeping production buffering/EOU logic live."""

    input_sample_rate = 1_000

    def __init__(self):
        self.fed = []
        self.closed = False
        self.done = False
        self.emitted = False

    def feed(self, samples):
        self.fed.append(np.array(samples, dtype=np.float32, copy=True))

    def step(self, *, max_decode_tokens=4):
        if not self.emitted and self.fed and np.max(np.abs(self.fed[0])) > 0.1:
            self.emitted = True
            return ["Switch to Terminal"]
        if self.closed:
            self.done = True
        return []

    def close(self):
        self.closed = True

    def cancel(self):
        self.done = True


class FakeSpeechModel:
    """Create independent utterance sessions like the production Nemotron model."""

    def __init__(self):
        self.sessions = []

    def create_streaming_session(self, *, temperature=0.0, language=None):
        session = FakeStreamingSession()
        self.sessions.append(session)
        return session


class FakeMicrophone:
    """Push a deterministic spoken block followed by enough silence for EOU."""

    def __init__(self, sample_rate, callback):
        self.sample_rate = sample_rate
        self.callback = callback
        self.done = False

    def start(self):
        self.callback(np.full((100, 1), 0.5, dtype=np.float32))
        self.callback(np.zeros((100, 1), dtype=np.float32))
        self.callback(np.zeros((100, 1), dtype=np.float32))
        self.done = True

    def stop(self):
        pass


class SpeechToActionEndToEnd(unittest.TestCase):
    """Leave a repeatable artifact from microphone frames through a verified action."""

    def test_audio_eou_jev_and_verified_native_action(self):
        """Prove one EOU reaches the real goal pipeline and leave its JSON evidence."""

        artifact = Path(__file__).resolve().parents[1] / ".build/voice-e2e.json"
        desktop, client, model = FakeDesktop(), FakeJev(), FakeSpeechModel()
        output = io.StringIO()
        status = main(
            ["--listen", "--yes", "--silence-ms", "200", "--report", str(artifact)],
            desktop=desktop,
            client=client,
            output=output,
            speech_model=model,
            audio_source_factory=FakeMicrophone,
        )

        report = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(report["mode"], "voice")
        self.assertEqual(len(report["runs"]), 1)
        self.assertEqual(report["runs"][0]["transcript"], "Switch to Terminal")
        self.assertEqual(report["runs"][0]["outcome"], "completed")
        self.assertTrue(report["runs"][0]["verified"])
        self.assertEqual(client.calls, 1)
        self.assertEqual(desktop.effects, 1)
        self.assertGreaterEqual(len(model.sessions[0].fed), 3)
        self.assertIn("Switch to Terminal", output.getvalue())

    def test_stop_after_provider_response_blocks_native_effect(self):
        """A Stop during Jev cannot allow a late response to dispatch an effect."""

        stopping = Event()
        desktop, client = FakeDesktop(), FakeJev(after_call=stopping.set)
        result = run_goal(
            "Switch to Terminal", desktop, client=client, cancel_event=stopping,
            approve=lambda _: True,
        )
        self.assertEqual(result["outcome"], "cancelled")
        self.assertEqual(client.calls, 1)
        self.assertEqual(desktop.effects, 0)

    def test_transcribe_only_does_not_need_jev_or_desktop_access(self):
        """Exercise local speech mode without constructing desktop or provider services."""

        artifact = Path(__file__).resolve().parents[1] / ".build/voice-transcribe-only-e2e.json"
        output = io.StringIO()
        status = main(
            ["--transcribe-only", "--silence-ms", "200", "--report", str(artifact)],
            output=output,
            speech_model=FakeSpeechModel(),
            audio_source_factory=FakeMicrophone,
        )
        report = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(report["mode"], "transcribe_only")
        self.assertEqual(report["transcripts"], ["Switch to Terminal"])
        self.assertEqual(report["runs"], [])


if __name__ == "__main__":
    unittest.main()
