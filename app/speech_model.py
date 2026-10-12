"""Load the local MLX Nemotron ASR model only when voice mode is requested."""

from __future__ import annotations

import platform


DEFAULT_MODEL = "mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit"


def load_speech_model(model_id: str = DEFAULT_MODEL) -> object:
    """Load Nemotron from the Hugging Face cache and require its streaming API."""

    if platform.system() != "Darwin" or platform.machine() not in {"arm64", "aarch64"}:
        raise RuntimeError("MLX-Audio speech recognition requires an Apple Silicon Mac.")
    try:
        from mlx_audio.stt import load
    except ImportError as error:
        raise RuntimeError(
            "MLX-Audio is not installed in this Python environment; run "
            "python -m pip install -r requirements.txt."
        ) from error

    model = load(model_id)
    if not callable(getattr(model, "create_streaming_session", None)):
        raise RuntimeError(
            f"{model_id} did not expose MLX-Audio's streaming-session API. "
            "Install the pinned project requirements and retry."
        )
    return model
