from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

_API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
_MODEL_ID = "scribe_v1"


def run(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    """Transcribe an audio file using ElevenLabs Speech-to-Text (Scribe v1).

    Requires ELEVENLABS_API_KEY environment variable.
    Returns transcript text and word-level timestamps (start/end in seconds).
    """
    audio_path = Path(tool_input["audio_path"])
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY environment variable is not set")

    params: dict[str, str] = {"model_id": _MODEL_ID}
    if language_code := tool_input.get("language_code"):
        params["language_code"] = language_code

    with audio_path.open("rb") as audio_file:
        response = httpx.post(
            _API_URL,
            headers={"xi-api-key": api_key},
            data=params,
            files={"file": ("audio.webm", audio_file, "audio/webm;codecs=opus")},
            timeout=55.0,
        )

    response.raise_for_status()
    payload = response.json()

    words = [
        {
            "text": w.get("text", ""),
            "start": float(w.get("start", 0)),
            "end": float(w.get("end", 0)),
            "type": w.get("type", "word"),
        }
        for w in payload.get("words", [])
    ]

    return {
        "transcript": payload.get("text", ""),
        "words": words,
        "language_code": payload.get("language_code", ""),
    }
