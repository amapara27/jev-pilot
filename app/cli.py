"""Expose text-goal and live-speech entry points to Jev's bounded desktop path."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
import json
import os
import queue
import select
import sys
import tempfile
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TextIO
from time import monotonic

import ApplicationServices as AX
from AppKit import NSApplicationActivateIgnoringOtherApps
from Foundation import NSDate, NSRunLoop
from .models import ActionCandidate
from .config import user_data_directory

from .desktop import MacDesktop
from .runtime import prepare_draft, run_goal
from .safety import requires_confirmation


def _parser() -> argparse.ArgumentParser:
    """Keep semantic inspection and app execution on the same command path."""

    parser = argparse.ArgumentParser(description="Ask Jev for a grounded operation, target, and exact payload.")
    parser.add_argument("goal", nargs="*", help="Text goal, such as 'switch to Finder'.")
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument("--dry-run", action="store_true", help="Choose without executing.")
    parser.add_argument("--yes", action="store_true", help="Skip routine confidence prompts; keep risky interactions and unfamiliar controls interactive.")
    parser.add_argument('--auto-approve-basic', action='store_true', help='Skip confidence prompts for app opening/focus and supported web/Finder search; retain all native guards and other approvals.')
    parser.add_argument("--request-accessibility", action="store_true", help="Ask macOS for Accessibility access for this CLI host.")
    parser.add_argument("--listen", action="store_true", help="Continuously transcribe microphone utterances and send each final transcript to Jev.")
    parser.add_argument("--transcribe-only", action="store_true", help="Listen and print transcripts without calling Jev or controlling the desktop.")
    parser.add_argument("--stream-prefixes", action="store_true", help="With --listen, queue stable app-opening clauses before EOU; completed effects cannot be taken back.")
    parser.add_argument("--draft", action=argparse.BooleanOptionalAction, default=False,
                        help="With --listen, try a draft Jev decision during a pause.")
    parser.add_argument("--push-to-talk", action="store_true", help="Hold Right Option to speak, then release it to finish the utterance.")
    parser.add_argument("--silence-ms", type=int, default=650, help="Quiet audio needed to commit an utterance (default: 650 ms).")
    parser.add_argument("--speech-threshold", type=float, default=0.012, help="RMS threshold for detecting speech (default: 0.012).")
    parser.add_argument("--report", type=Path, default=user_data_directory() / "Reports" / "python-cli-last.json",
                        help="Save the session report to this path (default: %(default)s).")
    return parser


def _save_report(path: Path, report: dict[str, object]) -> None:
    """Write one compact artifact atomically, including failure outcomes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")
    os.replace(temporary, path)


def _confirm_action(desktop, prompt, *, cancel_event=None):
    """Allow voice cancellation while waiting, then restore the exact approved process."""

    original = desktop.frontmost()
    if not original:
        return False
    pid = int(original.processIdentifier())
    if cancel_event is None:
        answer = input(prompt)
    else:
        print(prompt, end='', flush=True)
        while not cancel_event.is_set():
            if select.select([sys.stdin], [], [], 0.05)[0]:
                answer = sys.stdin.readline()
                break
        else:
            return False
    if (cancel_event is not None and cancel_event.is_set()) or answer.strip().casefold() != 'y':
        return False
    current = desktop.frontmost()
    if current and int(current.processIdentifier()) == pid:
        return True
    if not original.activateWithOptions_(NSApplicationActivateIgnoringOtherApps):
        return False
    deadline = monotonic() + 2
    while monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return False
        current = desktop.frontmost()
        if current and int(current.processIdentifier()) == pid:
            return True
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
    return False


def _run_voice(
    args: argparse.Namespace,
    *,
    desktop: object | None,
    client: object | None,
    output: TextIO,
    speech_model: object | None,
    audio_source_factory: object | None,
    key_source_factory=None,
    approve_action=None,
) -> int:
    """Run local ASR continuously and hand committed transcripts to one FIFO worker."""

    from .speech_model import load_speech_model
    from .speech_runtime import AudioInputError, StreamingCommands, run_live_asr

    try:
        model = speech_model if speech_model is not None else load_speech_model()
    except Exception as caught:
        result = {"schema_version": 1, "mode": "voice", "runs": [], "transcripts": [], "error": str(caught)}
        _save_report(args.report, result)
        print(f"Voice setup error: {caught}", file=output)
        return 1
    target_desktop = desktop if desktop is not None else (None if args.transcribe_only else MacDesktop())
    stopping = Event()
    capture_done = Event()
    approval_wait = Event()
    committed = queue.Queue(maxsize=5)
    transcripts: list[str] = []
    runs: list[dict[str, object]] = []
    pending: list[str] = []
    rejected: list[str] = []
    capture_error: list[str] = []
    worker_error: list[str] = []
    utterance_count = [0]
    prefixes = []
    draft_pool = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-draft")
                  if args.draft and not (args.transcribe_only or args.stream_prefixes) else None)
    draft_lock = Lock()
    draft_slot = None
    draft = None
    draft_turn = None
    attempt_turn = None
    attempts = draft_calls = draft_hits = 0
    turn = 0
    ended_at = None
    if args.push_to_talk and key_source_factory is None:
        from .push_to_talk import RightOptionTap
        key_source_factory = RightOptionTap

    def stop_phrase(text):
        """Keep local stop commands out of both provider paths."""

        return text.strip().casefold().strip('.!?') in {'stop listening', 'stop pilot', 'quit pilot'}

    def on_pause(text):
        """Publish one latest partial for the main thread without reading the desktop."""

        nonlocal draft_slot
        if not stop_phrase(text):
            with draft_lock:
                draft_slot = (turn, text)

    def on_boundary(timestamp):
        """End the draft's utterance even when it has no final transcript."""

        nonlocal draft_slot, turn, ended_at
        with draft_lock:
            draft_slot = None
            turn += 1
            ended_at = timestamp

    def start_draft():
        """Observe only while idle, with at most one outstanding provider request."""

        nonlocal draft_slot, draft, draft_turn, attempt_turn, attempts, draft_calls
        if draft_pool is None or capture_done.is_set() or not committed.empty() or (draft and not draft.future.done()):
            return
        with draft_lock:
            item, draft_slot = draft_slot, None
        if item is None:
            return
        item_turn, text = item
        if item_turn != attempt_turn:
            attempt_turn, attempts = item_turn, 0
        if attempts >= 2:
            return
        attempts += 1
        try:
            candidate = prepare_draft(text, target_desktop, draft_pool.submit, model=args.model, client=client)
        except Exception:
            # The final path reports any error that persists after a fresh observation.
            return
        if candidate is not None:
            draft, draft_turn = candidate, item_turn
            draft_calls += 1

    def commit(text, prefix=False, intended_bundle=None):
        """Queue text and app context; partial clauses share the same five-goal bound."""

        try:
            committed.put_nowait((text, prefix, intended_bundle, None if prefix else turn - 1,
                                  None if prefix else ended_at))
            if prefix:
                prefixes.append(text)
                print(f"\nQueued early: {text}", file=output, flush=True)
        except queue.Full as error:
            rejected.append(text)
            stopping.set()
            raise AudioInputError("The five-command goal queue is full; listening stopped and this instruction was rejected.") from error

    stream = StreamingCommands(target_desktop.application_aliases(), commit) if args.stream_prefixes else None

    def on_partial(text: str) -> None:
        """Show hypotheses and optionally queue a stable app clause."""

        if not approval_wait.is_set():
            print(f"\rPartial: {text}", end="", file=output, flush=True)
        if stream is not None:
            stream.partial(text)

    def on_final(text: str) -> None:
        """Queue final text or its unconsumed suffix, rejecting overload explicitly."""

        transcripts.append(text)
        print(f"\nTranscript: {text}", file=output, flush=True)
        if not args.transcribe_only and stop_phrase(text):
            stopping.set()
            return
        if args.transcribe_only:
            return
        if stream is not None:
            stream.final(text)
        else:
            commit(text)

    def capture() -> None:
        """Keep model decoding/capture off the AppKit execution thread."""

        try:
            utterance_count[0] = run_live_asr(
                model,
                on_final,
                on_partial=on_partial,
                on_pause=on_pause if draft_pool is not None else None,
                on_boundary=on_boundary,
                stop_event=stopping,
                eou_silence_ms=args.silence_ms,
                speech_threshold=args.speech_threshold,
                key_source_factory=key_source_factory,
                **({"audio_source_factory": audio_source_factory} if audio_source_factory else {}),
            )
        except Exception as caught:
            capture_error.append(str(caught) or f"ASR runtime failed ({type(caught).__name__}).")
            stopping.set()
        finally:
            capture_done.set()

    def approve(action: ActionCandidate) -> bool:
        """Skip routine confidence prompts with --yes; retain mandatory approvals."""

        if approve_action is not None:
            return approve_action(action)
        if args.yes and not requires_confirmation(action):
            return True
        if not sys.stdin.isatty():
            return False
        approval_wait.set()
        print(f"Confirm spoken action: {action.description} [y/N] ", end="", file=output, flush=True)
        try:
            return _confirm_action(target_desktop, '', cancel_event=stopping)
        finally:
            approval_wait.clear()

    def execute(item) -> None:
        """Run one queued transcript synchronously on the main/AppKit thread."""

        nonlocal draft, draft_slot, draft_hits
        goal, prefix, intended_bundle, goal_turn, speech_ended_at = item
        current_draft, draft = draft, None
        if current_draft is not None and draft_turn != goal_turn:
            while not current_draft.future.done() and not stopping.is_set():
                wait((current_draft.future,), timeout=0.05)
            current_draft = None
        if stopping.is_set():
            pending.append(goal)
            return
        result = run_goal(
            goal,
            target_desktop,
            model=args.model,
            dry_run=args.dry_run,
            approve=approve,
            client=client,
            cancel_event=stopping,
            allowed_operations={"OPEN_APP", "FOCUS_APP", "STOP"} if prefix else None,
            intended_bundle=intended_bundle if not prefix else None,
            stream_app=intended_bundle if prefix else None,
            auto_approve_basic=args.auto_approve_basic,
            draft=current_draft,
            speech_ended_at=speech_ended_at,
        )
        if draft_pool is not None:
            result.setdefault("draft", {"status": "none", "wait_ms": 0})
            draft_hits += result["draft"]["status"] == "hit"
        entry = {"transcript": goal, "source": "stable_prefix" if prefix else "final", **result}
        runs.append(entry)
        print(json.dumps(entry, indent=2, sort_keys=True), file=output, flush=True)
        if result["outcome"] not in {"completed", "dry_run"}:
            worker_error.append(str(result.get("reason", result["outcome"])))
            with draft_lock:
                draft_slot = None
            # Queued follow-ups may depend on this effect; only newly spoken goals resume.
            while not committed.empty():
                pending.append(committed.get_nowait()[0])
                committed.task_done()
            if prefix:
                stopping.set()
            elif not stopping.is_set():
                print('Command stopped. Still listening for a new command.', file=output, flush=True)
        else:
            print('Ready for the next command.', file=output, flush=True)
        _save_report(args.report, voice_report())

    def voice_report():
        """Persist the session after each goal so continuous testing leaves evidence."""

        result = {
            'schema_version': 1, 'mode': 'transcribe_only' if args.transcribe_only else 'voice',
            'model': getattr(model, 'model_id', 'mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit'),
            'transcripts': list(transcripts), 'committed_prefixes': list(prefixes),
            'rejected_transcripts': list(rejected), 'runs': list(runs),
            'pending_not_run': list(pending), 'utterances': max(utterance_count[0], len(transcripts)),
        }
        if worker_error:
            result['worker_error'] = worker_error[-1]
        if draft_pool is not None:
            result['drafts'] = {'calls': draft_calls, 'wasted': draft_calls - draft_hits}
        return result

    error = None
    if args.transcribe_only:
        try:
            source_kwargs = {"eou_silence_ms": args.silence_ms, "speech_threshold": args.speech_threshold,
                             "key_source_factory": key_source_factory}
            if audio_source_factory is not None:
                source_kwargs["audio_source_factory"] = audio_source_factory
            utterance_count[0] = run_live_asr(
                model, on_final, on_partial=on_partial, stop_event=stopping, **source_kwargs
            )
        except KeyboardInterrupt:
            stopping.set()
            print("\nListening stopped; unfinished speech was discarded.", file=output)
        except Exception as caught:
            stopping.set()
            error = str(caught) or f"Voice runtime failed ({type(caught).__name__})."
            print(f"\nVoice error: {error}", file=output)
    else:
        capture_thread = Thread(target=capture, name="nemotron-streaming-asr", daemon=True)
        capture_thread.start()
        try:
            while not stopping.is_set() and (not capture_done.is_set() or not committed.empty()):
                try:
                    goal = committed.get(timeout=0.05)
                except queue.Empty:
                    if not stopping.is_set():
                        start_draft()
                    continue
                try:
                    execute(goal)
                    if stopping.is_set():
                        break
                finally:
                    committed.task_done()
        except KeyboardInterrupt:
            stopping.set()
            print("\nListening stopped; unfinished speech was discarded.", file=output)
        finally:
            if draft_pool is not None:
                draft_pool.shutdown(wait=False, cancel_futures=True)
            if stopping.is_set():
                capture_thread.join()
            elif capture_done.is_set():
                capture_thread.join()
        if capture_error:
            error = capture_error[-1]
            print(f"\nVoice error: {error}", file=output)
        while not committed.empty():
            pending.append(committed.get_nowait()[0])
            committed.task_done()

    result = voice_report()
    if error:
        result["error"] = error
    if worker_error:
        result["worker_error"] = worker_error[-1]
    _save_report(args.report, result)
    print(json.dumps({key: result[key] for key in ("mode", "utterances", "pending_not_run", "error", "worker_error") if key in result}, indent=2), file=output)
    if error or worker_error or pending or rejected:
        return 1
    return 0


def main(
    argv: list[str] | None = None,
    *,
    desktop: object | None = None,
    client: object | None = None,
    output: TextIO = sys.stdout,
    speech_model: object | None = None,
    audio_source_factory: object | None = None,
) -> int:
    """Run one text goal or an explicit live voice session; typed mode stays unchanged."""

    args = _parser().parse_args(argv)
    if args.stream_prefixes and (not args.listen or args.transcribe_only):
        _parser().error("--stream-prefixes requires --listen without --transcribe-only")
    if args.push_to_talk and (not (args.listen or args.transcribe_only) or args.stream_prefixes):
        _parser().error("--push-to-talk requires --listen or --transcribe-only and cannot be combined with --stream-prefixes")
    if args.request_accessibility:
        trusted = bool(AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True}))
        print(json.dumps({"python_executable": sys.executable, "accessibility_trusted": trusted}, indent=2), file=output)
        return 0 if trusted else 1
    if args.listen or args.transcribe_only:
        if args.goal:
            _parser().error("do not combine a positional text goal with microphone mode")
        return _run_voice(
            args, desktop=desktop, client=client, output=output,
            speech_model=speech_model, audio_source_factory=audio_source_factory,
        )
    if not args.goal:
        _parser().error("a text goal is required unless --request-accessibility is used")
    goal = " ".join(args.goal)
    target_desktop = desktop if desktop is not None else MacDesktop()

    def approve(action: ActionCandidate) -> bool:
        """Apply the same routine/risky approval boundary as listening mode."""

        if args.yes and not requires_confirmation(action):
            return True
        if not sys.stdin.isatty():
            return False
        if action.kind == "TYPE_TEXT":
            # Show the exact locally resolved payload before low-confidence approval.
            print(f"Text: {action.parameters['text']!r}")
        return _confirm_action(target_desktop, f"Approve {action.description} [y/N] ")

    try:
        report = run_goal(
            goal,
            target_desktop,
            model=args.model,
            dry_run=args.dry_run,
            approve=approve,
            client=client,
            auto_approve_basic=args.auto_approve_basic,
        )
    except KeyboardInterrupt:
        report = {"schema_version": 3, "goal": goal, "outcome": "cancelled", "reason": "Interrupted; an already sent effect cannot be undone."}
    _save_report(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True), file=output)
    return 0 if report["outcome"] in {"completed", "dry_run"} else 1
