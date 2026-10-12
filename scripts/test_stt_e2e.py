"""Gate a real Nemotron audio-file transcription and normalized WER check."""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from app.cli import _save_report
from app.speech_model import DEFAULT_MODEL, load_speech_model
from app.speech_runtime import transcribe_audio_file


def normalize_words(text: str) -> list[str]:
    """Compare words while ignoring case, punctuation, and repeated spacing."""

    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.findall(r"\w+", folded, flags=re.UNICODE)


def word_error(reference: str, hypothesis: str) -> dict[str, object]:
    """Return minimum edit operations and word error rate for two transcripts."""

    expected, actual = normalize_words(reference), normalize_words(hypothesis)
    rows = [[(0, 0, 0, 0) for _ in range(len(actual) + 1)] for _ in range(len(expected) + 1)]
    for i in range(1, len(expected) + 1):
        rows[i][0] = (i, 0, 0, i)
    for j in range(1, len(actual) + 1):
        rows[0][j] = (j, 0, j, 0)
    for i, word in enumerate(expected, 1):
        for j, spoken in enumerate(actual, 1):
            if word == spoken:
                rows[i][j] = rows[i - 1][j - 1]
                continue
            candidates = (
                (rows[i - 1][j - 1][0] + 1, rows[i - 1][j - 1][1] + 1,
                 rows[i - 1][j - 1][2], rows[i - 1][j - 1][3]),
                (rows[i][j - 1][0] + 1, rows[i][j - 1][1],
                 rows[i][j - 1][2] + 1, rows[i][j - 1][3]),
                (rows[i - 1][j][0] + 1, rows[i - 1][j][1],
                 rows[i - 1][j][2], rows[i - 1][j][3] + 1),
            )
            rows[i][j] = min(candidates, key=lambda result: result[0])
    edits, substitutions, insertions, deletions = rows[-1][-1]
    return {
        "reference_words": len(expected),
        "hypothesis_words": len(actual),
        "substitutions": substitutions,
        "insertions": insertions,
        "deletions": deletions,
        "word_error_rate": edits / len(expected) if expected else (0.0 if not actual else 1.0),
    }


def main() -> int:
    """Transcribe only when an explicit audio fixture and reference are provided."""

    audio = os.environ.get("JEV_STT_AUDIO")
    expected = os.environ.get("JEV_STT_EXPECTED")
    if not audio or not expected:
        print("Set JEV_STT_AUDIO=/absolute/path/sample.wav and JEV_STT_EXPECTED='spoken words'.", file=sys.stderr)
        return 2
    audio_path = Path(audio).expanduser().resolve()
    if not audio_path.is_file():
        print(f"Audio fixture does not exist: {audio_path}", file=sys.stderr)
        return 2

    model = load_speech_model()
    transcript = transcribe_audio_file(model, str(audio_path))
    score = word_error(expected, transcript)
    threshold = float(os.environ.get("JEV_STT_MAX_WER", "0.20"))
    report = {
        "schema_version": 1,
        "model": DEFAULT_MODEL,
        "audio": str(audio_path),
        "expected": expected,
        "transcript": transcript,
        **score,
        "max_word_error_rate": threshold,
        "outcome": "passed" if transcript and score["word_error_rate"] <= threshold else "failed",
    }
    artifact = Path(".build/stt-e2e.json")
    _save_report(artifact, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
