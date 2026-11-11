"""Prove captured audio reaches Jev and a verified desktop effect, without MLX or live I/O."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread, get_ident
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.cli import _parser, _run_voice, main
from app.runtime import run_goal
from test_cli_e2e import FakeDesktop, FakeJev
from test_typing_e2e import Jev, setup


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

    def test_draft_reuses_only_exact_final_text_and_reobserves_stale_caret(self):
        """Use real decoding callbacks and native routing, with barriers around the provider."""

        for scenario, draft_status, suffix, expected, expected_calls in (
            ("hit", "hit", "", "before hello", 1),
            ("miss_text", "miss_text", " world", "before hello world", 2),
            ("stale_fallback", "stale_fallback", "", "hellobefore ", 2),
            ("uncertain_dispatch", "hit", "", "before hello", 1),
        ):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                desktop, ax, _, _ = setup()
                ax.setter_error = scenario == "uncertain_dispatch"
                draft_requested, decoder_closed = Event(), Event()
                provider_threads, pre_final_effects, pre_commit_effects = [], [], []
                main_thread = get_ident()

                class Session(FakeStreamingSession):
                    def step(self, *, max_decode_tokens=4):
                        if not self.emitted and self.fed:
                            self.emitted = True
                            return ["type hello"]
                        if self.closed and not self.done:
                            pre_commit_effects.append(list(ax.effects))
                            self.done = True
                            decoder_closed.set()
                            return [suffix] if suffix else []
                        return []

                class Microphone:
                    done = False
                    error = None

                    def __init__(self, rate, callback):
                        self.callback = callback

                    def start(self):
                        self.callback(np.full(100, 0.5, dtype=np.float32))
                        self.callback(np.zeros(160, dtype=np.float32))
                        self.thread = Thread(target=self.finish, daemon=True)
                        self.thread.start()

                    def finish(self):
                        if not draft_requested.wait(3):
                            self.error = "No Jev request arrived during the draft pause."
                        self.callback(np.zeros(500, dtype=np.float32))
                        self.done = True

                    def stop(self):
                        self.thread.join()

                def after_call():
                    provider_threads.append(get_ident())
                    if len(provider_threads) == 1:
                        pre_final_effects.append(list(ax.effects))
                        if scenario == "stale_fallback":
                            ax.editor.attrs["AXSelectedTextRange"] = (0, 0)
                        draft_requested.set()
                        if not decoder_closed.wait(3):
                            raise RuntimeError("The decoder did not finish while Jev was pending.")
                    elif not decoder_closed.is_set():
                        raise RuntimeError("A second request started before final decoding.")

                model = SimpleNamespace(create_streaming_session=lambda **_: Session())
                report_path = Path(directory) / "draft.json"
                client = Jev(after_call=after_call)
                status = main(["--listen", "--draft", "--report", str(report_path)],
                              desktop=desktop, client=client, output=io.StringIO(),
                              speech_model=model, audio_source_factory=Microphone)
                report = json.loads(report_path.read_text())

                self.assertEqual(status, int(ax.setter_error), report)
                self.assertEqual(report["transcripts"], ["type hello" + suffix])
                self.assertEqual(len(report["runs"]), 1)
                run = report["runs"][0]
                self.assertEqual(run["draft"]["status"], draft_status)
                self.assertEqual(report["drafts"], {"calls": 1, "wasted": int(draft_status != "hit")})
                self.assertEqual(len(client.calls), expected_calls)
                self.assertNotEqual(provider_threads[0], main_thread)
                self.assertEqual(provider_threads[1:], [main_thread] * (expected_calls - 1))
                self.assertEqual(pre_final_effects, [[]])
                self.assertEqual(pre_commit_effects, [[]])
                self.assertEqual(len(run["steps"]), 1)
                self.assertTrue(run["effect_sent"])
                self.assertEqual(run["verified"], not ax.setter_error)
                if ax.setter_error:
                    self.assertEqual(run["outcome"], "failed")
                    self.assertIn("uncertain", run["reason"])
                else:
                    self.assertGreaterEqual(run["speech_end_to_effect_ms"], 0)
                self.assertEqual(ax.effects, [("AXSelectedText", "hello" + suffix)])
                self.assertEqual(ax.editor.attrs["AXValue"], expected)

    def test_push_to_talk_discards_chords_and_silent_holds_and_commits_on_release(self):
        """Prove the audio gate, tail and desktop effect through the live CLI."""

        from app.push_to_talk import Quartz, RightOptionTap

        desktop, client = FakeDesktop(), FakeJev()
        keys, during_hold, returned_events = [], [], []

        def key_source(on_key):
            keys.append(RightOptionTap(on_key))
            return SimpleNamespace(start=lambda: None, stop=lambda: None)

        class Microphone:
            done = False

            def __init__(self, rate, callback):
                self.callback = callback

            def start(self):
                def audio(length, value):
                    self.callback(np.full(length, value, dtype=np.float32))

                def flags(code, value):
                    event = (code, value)
                    returned_events.append(keys[0]._event(None, Quartz.kCGEventFlagsChanged, event, None) is event)

                right, left = Quartz.kCGEventFlagMaskAlternate | 0x40, Quartz.kCGEventFlagMaskAlternate | 0x20
                audio(400, 0.9)
                flags(58, left)
                flags(61, right | left)
                audio(100, 0.6)
                keys[0]._event(None, Quartz.kCGEventKeyDown, (0, right | left), None)
                audio(200, 0.7)
                flags(61, left)
                audio(400, 0.8)
                flags(61, right | left)
                audio(100, 0)
                flags(61, left)
                audio(150, 0)
                flags(58, 0)
                audio(100, 0.3)
                flags(61, right)
                audio(100, 0.5)
                audio(800, 0)
                flags(61, 0)
                audio(200, 0.1)
                self.done = True

            def stop(self):
                pass

        class Session(FakeStreamingSession):
            def step(self, *, max_decode_tokens=4):
                if len(self.fed) == 3 and not self.closed and self.fed[-1].size == 800:
                    during_hold.append(desktop.effects)
                return super().step(max_decode_tokens=max_decode_tokens)

        class Model(FakeSpeechModel):
            def create_streaming_session(self, **kwargs):
                session = Session()
                self.sessions.append(session)
                return session

        model = Model()
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(Quartz, "CGEventGetIntegerValueField", side_effect=lambda event, _: event[0]),
              patch.object(Quartz, "CGEventGetFlags", side_effect=lambda event: event[1])):
            path = Path(directory) / "push-to-talk.json"
            args = _parser().parse_args(["--listen", "--push-to-talk", "--yes", "--report", str(path)])
            status = _run_voice(args, desktop=desktop, client=client, output=io.StringIO(),
                                speech_model=model, audio_source_factory=Microphone, key_source_factory=key_source)
            report = json.loads(path.read_text())

        self.assertEqual(status, 0, report)
        self.assertEqual(returned_events, [True] * 8)
        self.assertEqual(during_hold, [0])
        self.assertEqual(report["transcripts"], ["Switch to Terminal"])
        self.assertEqual(client.calls, 1)
        self.assertEqual(desktop.effects, 1)
        self.assertTrue(report["runs"][0]["verified"])
        self.assertGreaterEqual(report["runs"][0]["speech_end_to_effect_ms"], 0)
        first, second, third, idle = model.sessions
        self.assertFalse(first.closed)
        self.assertFalse(second.closed)
        self.assertTrue(third.closed)
        self.assertFalse(idle.fed)
        for session, blocks in (
            (first, [(300, 0.9), (100, 0.6)]),
            (second, [(300, 0.8), (100, 0), (150, 0)]),
            (third, [(100, 0.3), (100, 0.5), (800, 0), (150, 0.1)]),
        ):
            self.assertEqual(len(session.fed), len(blocks))
            for actual, (length, value) in zip(session.fed, blocks):
                np.testing.assert_array_equal(actual, np.full(length, value, dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
