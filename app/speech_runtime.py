"""Capture bounded microphone audio and stream utterances through Nemotron ASR."""

from __future__ import annotations

from collections.abc import Callable
from queue import Empty, Full, Queue
from threading import Event
import re
from time import monotonic

import numpy as np


AUDIO_BLOCK_SAMPLES = 1_024
MAX_AUDIO_BLOCKS = 64
DEFAULT_EOU_SILENCE_MS = 650
DEFAULT_SPEECH_THRESHOLD = 0.012


class AudioInputError(RuntimeError):
    """Describe capture, buffering, or stream-session failures clearly."""


class StreamingCommands:
    """Commit one stable, complete app clause, then only its unconsumed final suffix."""

    def __init__(self, aliases, commit, *, clock=None):
        self.aliases, self.commit, self.clock = aliases, commit, clock
        self.candidate = None
        self.since = 0.0
        self.committed = None

    def partial(self, text):
        """Hold an explicitly delimited app clause for 400 ms before queueing it."""

        if self.committed is not None:
            if not text.startswith(self.committed[0]):
                raise AudioInputError("Speech revised an already committed app clause; stopped. Completed effects remain.")
            return
        # shortcut: only the first explicit English app clause streams; extend after live acceptance.
        match = re.match(r"^(?:open|launch|focus|switch to)\s+(.+?)(?=\s*(?:[,;]|\band(?:\s+then)?\b|\bthen\b))", text, re.IGNORECASE)
        candidate = None
        if match and match.group(1).strip().casefold() in self.aliases:
            candidate = (text[:match.end()].rstrip(), self.aliases[match.group(1).strip().casefold()])
        now = (self.clock or monotonic)()
        if candidate != self.candidate:
            self.candidate, self.since = candidate, now
        elif candidate and now - self.since >= 0.4:
            self.committed = candidate
            self.commit(candidate[0], True, candidate[1])

    def final(self, text):
        """Retain final payload bytes, remove only a committed clause and its connector."""

        if self.committed is None:
            self.commit(text, False, None)
        else:
            prefix, bundle = self.committed
            if not text.startswith(prefix):
                raise AudioInputError("Final speech changed an already committed app clause; stopped. Completed effects remain.")
            remainder = text[len(prefix):]
            connector = re.match(r"\s*(?:[,;]\s*|and(?:\s+then)?\b\s*|then\b\s*)", remainder, re.IGNORECASE)
            if not connector:
                raise AudioInputError("The final transcript no longer contains the committed clause boundary; stopped.")
            remainder = remainder[connector.end():]
            if remainder.strip():
                self.commit(remainder, False, bundle)
        self.candidate = self.committed = None


class SoundDeviceAudioSource:
    """Bridge PortAudio callbacks to a queue without running MLX in that callback."""

    def __init__(self, sample_rate: int, on_audio: Callable[[object], None]):
        self.sample_rate = sample_rate
        self.on_audio = on_audio
        self.error: str | None = None
        self.done = False
        self._stream = None

    def start(self) -> None:
        """Start mono float32 capture; the callback only copies and queues frames."""

        try:
            import sounddevice as sd
        except ImportError as error:
            raise AudioInputError(
                "Microphone capture requires sounddevice; reinstall requirements.txt."
            ) from error

        def callback(indata, frames, timing, status):
            """Keep the real-time callback bounded and defer errors to the consumer."""

            if status:
                self.error = str(status)
                return
            self.on_audio(indata[:, 0])

        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=AUDIO_BLOCK_SAMPLES,
                callback=callback,
            )
            self._stream.start()
        except Exception as error:
            raise AudioInputError(f"Could not start microphone input: {error}") from error

    def stop(self) -> None:
        """Stop capture and release the microphone device."""

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.done = True


def _rms(samples: np.ndarray) -> float:
    """Measure block energy for the lightweight local end-of-utterance gate."""

    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float32)))) if samples.size else 0.0


def _append_deltas(session: object, text: str) -> tuple[str, bool]:
    """Run one cooperative decoder step and append its newly emitted text tokens."""

    deltas = session.step(max_decode_tokens=4)
    return text + "".join(deltas), bool(deltas)


def run_live_asr(
    model: object,
    on_final: Callable[[str], None],
    *,
    on_partial: Callable[[str], None] = lambda _: None,
    stop_event: Event | None = None,
    audio_source_factory: Callable[[int, Callable[[object], None]], object] = SoundDeviceAudioSource,
    eou_silence_ms: int = DEFAULT_EOU_SILENCE_MS,
    speech_threshold: float = DEFAULT_SPEECH_THRESHOLD,
    max_audio_blocks: int = MAX_AUDIO_BLOCKS,
) -> int:
    """Consume one ordered audio queue, emitting one final transcript per EOU."""

    if eou_silence_ms <= 0 or max_audio_blocks <= 0 or not 0 <= speech_threshold <= 1:
        raise ValueError("EOU silence, audio queue capacity, and speech threshold are invalid.")
    stopping = stop_event or Event()
    first_session = model.create_streaming_session(temperature=0.0, language=None)
    sample_rate = int(first_session.input_sample_rate)
    audio_queue: Queue[np.ndarray] = Queue(maxsize=max_audio_blocks)
    overflow = Event()

    def queue_audio(samples: object) -> None:
        """Copy valid mono samples to the bounded queue or flag overload explicitly."""

        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not np.isfinite(audio).all():
            overflow.set()
            return
        try:
            audio_queue.put_nowait(audio.copy())
        except Full:
            overflow.set()

    source = audio_source_factory(sample_rate, queue_audio)
    session = first_session
    text = ""
    speaking = False
    quiet_samples = 0
    utterances = 0

    def finish_utterance() -> None:
        """Flush one EOU exactly once before opening an independent next session."""

        nonlocal session, text, speaking, quiet_samples, utterances
        session.close()
        while not session.done:
            text, changed = _append_deltas(session, text)
            if text:
                on_partial(text)
        final_text = text.strip()
        if final_text:
            on_final(final_text)
            utterances += 1
        session = model.create_streaming_session(temperature=0.0, language=None)
        text = ""
        speaking = False
        quiet_samples = 0

    try:
        source.start()
        while not stopping.is_set():
            if overflow.is_set():
                raise AudioInputError(
                    "Microphone audio backlog exceeded its limit; no audio was silently dropped."
                )
            source_error = getattr(source, "error", None)
            if source_error:
                raise AudioInputError(f"Microphone input failed: {source_error}")
            try:
                audio = audio_queue.get(timeout=0.02)
            except Empty:
                if getattr(source, "done", False) and audio_queue.empty():
                    break
                continue

            energy = _rms(audio)
            session.feed(audio)
            text, changed = _append_deltas(session, text)
            if text:
                on_partial(text)
            if energy >= speech_threshold:
                speaking = True
                quiet_samples = 0
            elif speaking:
                quiet_samples += audio.size
                if quiet_samples * 1_000 >= eou_silence_ms * sample_rate:
                    finish_utterance()

        if stopping.is_set():
            session.cancel()
        elif speaking and text.strip():
            finish_utterance()
        return utterances
    finally:
        source.stop()
        if not getattr(session, "done", False):
            session.cancel()


def transcribe_audio_file(model: object, audio_path: str, *, chunk_seconds: float = 0.1) -> str:
    """Feed a known audio file through the same Nemotron streaming-session API."""

    if chunk_seconds <= 0:
        raise ValueError("Audio chunk duration must be positive.")
    try:
        import mlx.core as mx
        from mlx_audio.stt.utils import load_audio
    except ImportError as error:
        raise AudioInputError("MLX-Audio is required to transcribe an audio file.") from error

    session = model.create_streaming_session(temperature=0.0, language=None)
    samples = np.asarray(
        load_audio(audio_path, session.input_sample_rate, dtype=mx.float32), dtype=np.float32
    ).reshape(-1)
    chunk_size = max(1, int(session.input_sample_rate * chunk_seconds))
    text = ""
    try:
        for offset in range(0, samples.size, chunk_size):
            session.feed(samples[offset : offset + chunk_size])
            text, _ = _append_deltas(session, text)
        session.close()
        while not session.done:
            text, _ = _append_deltas(session, text)
        return text.strip()
    except Exception:
        session.cancel()
        raise
