"""Capture bounded microphone audio and stream utterances through Nemotron ASR."""

from __future__ import annotations

from collections import deque
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
DRAFT_AFTER_MS = 150


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
                "Microphone capture requires sounddevice; reinstall Jev Pilot with its dependencies."
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
    on_pause: Callable[[str], None] | None = None,
    on_boundary: Callable[[float], None] = lambda _: None,
    on_state: Callable[[str, str], None] = lambda *_: None,
    stop_event: Event | None = None,
    audio_source_factory: Callable[[int, Callable[[object], None]], object] = SoundDeviceAudioSource,
    key_source_factory: Callable[[Callable[[str], None]], object] | None = None,
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
    audio_queue: Queue[tuple[np.ndarray | str, float]] = Queue(maxsize=max_audio_blocks)
    overflow = Event()

    def enqueue(item, captured_at):
        """Put keyboard transitions in the same ordered stream as audio blocks."""

        try:
            audio_queue.put_nowait((item, captured_at))
        except Full:
            overflow.set()

    def queue_audio(samples: object) -> None:
        """Copy valid mono samples to the bounded queue or flag overload explicitly."""

        captured_at = monotonic()
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not np.isfinite(audio).all():
            overflow.set()
            return
        enqueue(audio.copy(), captured_at)

    source = audio_source_factory(sample_rate, queue_audio)
    keys = key_source_factory(lambda key: enqueue(key, monotonic())) if key_source_factory else None
    session = first_session
    text = ""
    speaking = False
    quiet_samples = 0
    pause_reported = False
    speech_end = last_audio = monotonic()
    utterances = 0
    pressed = cancelled = held_speech = False
    release_at = None
    tail_samples = 0
    preroll = deque()
    preroll_samples = 0
    preroll_limit = sample_rate * 300 // 1_000

    def reset_session() -> None:
        """Open an independent ASR session after a commit or cancellation."""

        nonlocal session, text, speaking, quiet_samples, pause_reported, held_speech
        session = model.create_streaming_session(temperature=0.0, language=None)
        text = ""
        speaking = pause_reported = held_speech = False
        quiet_samples = 0

    def finish_utterance(ended_at: float) -> None:
        """Finalize once and publish the speech boundary with the exact transcript."""

        nonlocal utterances
        on_state('processing', 'Finishing transcript…')
        on_boundary(ended_at)
        session.close()
        final_text = text
        while not session.done and not stopping.is_set():
            final_text, _ = _append_deltas(session, final_text)
            if final_text:
                on_partial(final_text)
        final_text = final_text.strip()
        if not stopping.is_set() and final_text:
            on_final(final_text)
            utterances += 1
        elif not stopping.is_set():
            on_state('unheard', 'No words recognized. Try again, or lower --speech-threshold for quiet speech.')
        if not getattr(session, "done", False):
            session.cancel()
        reset_session()

    def feed(audio, captured_at, *, allow_pause=True):
        """Decode audio and signal the first eligible pause before finalization."""

        nonlocal text, speaking, quiet_samples, pause_reported, speech_end
        voiced = _rms(audio) >= speech_threshold
        session.feed(audio)
        text, _ = _append_deltas(session, text)
        if text:
            on_partial(text)
        if voiced:
            speaking, quiet_samples, pause_reported = True, 0, False
        elif speaking:
            if not quiet_samples:
                speech_end = captured_at - audio.size / sample_rate
            quiet_samples += audio.size
            if allow_pause and on_pause is not None and not pause_reported and text.strip() and quiet_samples * 1_000 >= DRAFT_AFTER_MS * sample_rate:
                pause_reported = True
                on_pause(text.strip())
        return voiced

    def buffer_preroll(audio):
        """Keep just the last 300 ms, even for audio sources with varying block sizes."""

        nonlocal preroll_samples
        if not audio.size:
            return
        preroll.append(audio)
        preroll_samples += audio.size
        while preroll_samples > preroll_limit:
            oldest = preroll.popleft()
            excess = preroll_samples - preroll_limit
            if oldest.size > excess:
                preroll.appendleft(oldest[excess:].copy())
            preroll_samples -= min(oldest.size, excess)

    def finish_release():
        """Commit only a released hold with speech captured while the key was down."""

        nonlocal release_at
        if held_speech:
            finish_utterance(release_at)
        else:
            on_boundary(release_at)
            session.cancel()
            reset_session()
        release_at = None

    try:
        if keys is not None:
            keys.start()
        source.start()
        while not stopping.is_set():
            if overflow.is_set():
                raise AudioInputError(
                    "Microphone audio backlog exceeded its limit; no audio was silently dropped."
                )
            source_error = getattr(source, "error", None)
            if source_error:
                raise AudioInputError(f"Microphone input failed: {source_error}")
            if keys is not None and getattr(keys, "error", None):
                raise AudioInputError(keys.error)
            try:
                item, captured_at = audio_queue.get(timeout=0.02)
            except Empty:
                if getattr(source, "done", False) and audio_queue.empty():
                    break
                continue

            if isinstance(item, str):
                if item == "press" and not pressed:
                    if release_at is not None:
                        finish_release()
                    pressed, cancelled = True, False
                    on_state('listening', 'Listening — release Right Option to finish')
                    for block in preroll:
                        feed(block, captured_at, allow_pause=False)
                    preroll.clear()
                    preroll_samples = 0
                elif item == "cancel" and pressed and not cancelled:
                    cancelled = True
                    on_state('cancelled', 'Shortcut detected — utterance cancelled')
                    on_boundary(captured_at)
                    session.cancel()
                    reset_session()
                elif item == "release" and pressed:
                    pressed = False
                    if not cancelled:
                        release_at = captured_at
                        tail_samples = sample_rate * 150 // 1_000
                continue

            if keys is None:
                if not speaking:
                    if _rms(item) < speech_threshold:
                        buffer_preroll(item)
                        continue
                    on_state('listening', 'Listening…')
                    for block in preroll:
                        feed(block, captured_at, allow_pause=False)
                    preroll.clear()
                    preroll_samples = 0
                feed(item, captured_at)
                last_audio = captured_at
                if quiet_samples * 1_000 >= eou_silence_ms * sample_rate:
                    finish_utterance(speech_end)
            elif pressed:
                if not cancelled:
                    held_speech |= feed(item, captured_at, allow_pause=held_speech)
            elif release_at is not None:
                consumed = min(item.size, tail_samples)
                if consumed:
                    feed(item[:consumed], captured_at - (item.size - consumed) / sample_rate, allow_pause=False)
                tail_samples -= consumed
                if not tail_samples:
                    finish_release()
                    buffer_preroll(item[consumed:])
            else:
                buffer_preroll(item)

        if stopping.is_set():
            session.cancel()
        elif keys is not None and release_at is not None:
            finish_release()
        elif keys is None and speaking:
            finish_utterance(speech_end if quiet_samples else last_audio)
        return utterances
    finally:
        try:
            if keys is not None:
                keys.stop()
        finally:
            try:
                source.stop()
            finally:
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
