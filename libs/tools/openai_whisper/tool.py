from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

_API_URL = "https://api.openai.com/v1/audio/transcriptions"
_DEFAULT_MODEL = "whisper-1"


def run(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    """Transcribe an audio file using OpenAI Whisper/audio transcriptions.

    Requires OPENAI_API_KEY. Returns transcript text and word-level timestamps in
    seconds when the OpenAI response includes them.
    """
    audio_path = Path(tool_input["audio_path"])
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set")

    data: dict[str, Any] = {
        "model": tool_input.get("model") or _DEFAULT_MODEL,
        "response_format": "verbose_json",
    }
    if language := tool_input.get("language"):
        data["language"] = language

    # OpenAI expects repeated form fields for timestamp granularities.
    form_data = list(data.items()) + [("timestamp_granularities[]", "word")]

    with audio_path.open("rb") as audio_file:
        response = httpx.post(
            _API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data=form_data,
            files={"file": (audio_path.name, audio_file, "application/octet-stream")},
            timeout=115.0,
        )

    response.raise_for_status()
    payload = response.json()

    words = [
        {
            "text": word.get("word") or word.get("text", ""),
            "start": float(word.get("start", 0)),
            "end": float(word.get("end", 0)),
            "type": word.get("type", "word"),
        }
        for word in payload.get("words", [])
    ]

    return {
        "transcript": payload.get("text", ""),
        "words": words,
        "language_code": payload.get("language", ""),
    }
