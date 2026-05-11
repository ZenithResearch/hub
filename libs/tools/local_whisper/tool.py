from __future__ import annotations

import os
from typing import Any

import httpx

_DEFAULT_STT_HTTP_URL = "http://stt-http:8765"
_DEFAULT_MODEL = "tiny"
_TIMEOUT_SECONDS = 300.0


def _stt_url() -> str:
    return os.environ.get("STT_HTTP_URL", _DEFAULT_STT_HTTP_URL).rstrip("/")


def _normalize_words(raw_words: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_words, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_words:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("word") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "text": text,
                "start": float(item.get("start") or 0),
                "end": float(item.get("end") or 0),
                "type": str(item.get("type") or "word"),
            }
        )
    return rows


def run(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    """Transcribe audio through the compose-internal local STT service.

    The heavy open-source Whisper/Torch dependency lives in the `stt-http`
    service. This tool remains the process contract surface and only forwards the
    audio path/model/language request over the internal compose network. It does
    not require an OpenAI API key.
    """
    audio_path = str(tool_input["audio_path"])
    model_name = str(tool_input.get("model") or os.environ.get("LOCAL_WHISPER_MODEL") or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL

    payload: dict[str, Any] = {"audio_path": audio_path, "model": model_name}
    if language := tool_input.get("language"):
        payload["language"] = language

    url = f"{_stt_url()}/transcribe"
    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() if exc.response is not None else str(exc)
        raise RuntimeError(f"local STT service rejected transcription request: {detail}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"local STT service unavailable at {url}: {exc}") from exc

    data = response.json()
    return {
        "transcript": str(data.get("transcript") or "").strip(),
        "words": _normalize_words(data.get("words")),
        "language_code": str(data.get("language_code") or data.get("language") or tool_input.get("language") or ""),
        "model": str(data.get("model") or model_name),
    }
