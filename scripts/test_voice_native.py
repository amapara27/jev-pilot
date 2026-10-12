"""Gate full audio-file → Nemotron → live Jev → disposable TextEdit insertion."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic, sleep

from app.cli import _save_report
from app.config import load_api_key
from app.speech_model import DEFAULT_MODEL, load_speech_model
from app.speech_runtime import transcribe_audio_file
from scripts.test_stt_e2e import word_error
from scripts.test_typing_native import FixtureDesktop, TEXTEDIT, readiness


def main() -> int:
    """Require explicit real audio, live provider credentials, and bounded native fixture."""

    audio = os.environ.get("JEV_STT_AUDIO")
    expected = os.environ.get("JEV_STT_EXPECTED")
    if not audio or not expected:
        print("Set JEV_STT_AUDIO=/absolute/path/command.wav and JEV_STT_EXPECTED='open TextEdit and type \"phrase\"'.", file=sys.stderr)
        return 2
    audio_path = Path(audio).expanduser().resolve()
    if not audio_path.is_file():
        print(f"Audio fixture does not exist: {audio_path}", file=sys.stderr)
        return 2
    threshold = float(os.environ.get("JEV_STT_MAX_WER", "0.20"))
    report: dict[str, object] = {
        "schema_version": 1,
        "model": DEFAULT_MODEL,
        "audio": str(audio_path),
        "expected": expected,
        "provider": "live Jev",
        "effect_boundary": "disposable TextEdit fixture only",
        "outcome": "failed",
        "effect_sent": False,
        "verified": False,
    }
    artifact = Path(".build/voice-native-e2e.json")
    desktop = FixtureDesktop()
    fixture_dir: Path | None = None
    try:
        if not load_api_key():
            raise RuntimeError("Configure TYPESAFE_API_KEY before running the live Jev test.")
        if not desktop.text.ax.AXIsProcessTrusted():
            raise RuntimeError("Grant Accessibility to this Python/terminal host before testing native typing.")
        starting = desktop.snapshot().active
        if starting is None or starting.bundle_id == TEXTEDIT:
            raise RuntimeError("Run this acceptance from another foreground app with TextEdit in the background.")

        transcript = transcribe_audio_file(load_speech_model(), str(audio_path))
        report["transcript"] = transcript
        report.update(word_error(expected, transcript))
        report["max_word_error_rate"] = threshold
        if not transcript or report["word_error_rate"] > threshold:
            raise RuntimeError("Nemotron transcript did not meet the configured WER threshold; no desktop effect was attempted.")
        if not transcript.lstrip().lower().startswith(("open textedit", "focus textedit", "switch to textedit")) or "type" not in transcript.casefold():
            raise RuntimeError("The verified transcript must explicitly open/focus TextEdit and type a quoted phrase; no desktop effect was attempted.")

        artifact.parent.mkdir(parents=True, exist_ok=True)
        fixture_dir = Path(tempfile.mkdtemp(prefix="jev-voice-", dir=artifact.parent)).resolve()
        document = fixture_dir / f"Jev disposable voice fixture {fixture_dir.name}.txt"
        initial = f"JEVVOICEFIXTURE-{fixture_dir.name}\n"
        document.write_text(initial, encoding="utf-8")
        desktop.document, desktop.initial = document, initial
        report["fixture"] = str(document)
        subprocess.run(["open", "-a", "TextEdit", str(document)], check=True)

        deadline = monotonic() + 8
        while monotonic() < deadline:
            state = desktop.snapshot()
            report["editor_readiness"] = readiness(state)
            if state.active and state.active.bundle_id == TEXTEDIT:
                application = desktop.text.ax.AXUIElementCreateApplication(state.active.pid)
                window = desktop.text.read(application, "AXFocusedWindow")
                if window is not None and fixture_dir.name in str(desktop.text.read(window, "AXTitle") or ""):
                    break
            sleep(0.05)
        else:
            raise RuntimeError("TextEdit did not expose the disposable voice fixture window.")

        subprocess.run(["open", "-b", starting.bundle_id], check=True)
        deadline = monotonic() + 8
        while monotonic() < deadline:
            if desktop.snapshot().active == starting:
                break
            sleep(0.05)
        else:
            raise RuntimeError("Could not restore the starting app before the voice command.")

        from app.runtime import run_goal
        result = run_goal(transcript, desktop, client=None)
        report.update(result)
        candidates = [
            step.get("decision", {}).get("selected_candidate")
            for step in report.get("steps", [])
        ]
        inserted = next(
            (candidate.get("parameters", {}).get("text") for candidate in candidates
             if candidate and candidate.get("kind") == "TYPE_TEXT"),
            None,
        )
        contents = document.read_text(encoding="utf-8")
        deadline = monotonic() + 3
        while inserted and inserted not in contents and monotonic() < deadline:
            sleep(0.05)
            contents = document.read_text(encoding="utf-8")
        report["fixture_contains_selected_text"] = bool(
            inserted and inserted in contents and initial.strip() in contents
        )
        report["effect_sent"] = bool(report.get("effect_sent"))
        report["verified"] = bool(report.get("verified") and report["fixture_contains_selected_text"])
        report["outcome"] = "passed" if report.get("outcome") == "completed" and report["verified"] else "failed"
        if report["outcome"] == "failed" and not report.get("reason"):
            report["reason"] = "The selected text was not verifiably inserted into the disposable fixture."
    except Exception as error:
        report["reason"] = str(error) if isinstance(error, RuntimeError) else f"Native voice E2E failed ({type(error).__name__})."
    finally:
        _save_report(artifact, report)
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
