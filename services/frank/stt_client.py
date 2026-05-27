from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_LOCAL_STT_URL = "http://stt-http:8765"
DEFAULT_LOCAL_MODEL = "tiny"
DEFAULT_ELEVENLABS_MODEL = "scribe_v2"
DEFAULT_ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
DEFAULT_ELEVENLABS_AUDIO_ISOLATION_URL = "https://api.elevenlabs.io/v1/audio-isolation"

_PROVIDER_ALIASES = {
    "local": "local_whisper",
    "local_whisper": "local_whisper",
    "stt-http": "local_whisper",
    "stt_http": "local_whisper",
    "elevenlabs": "elevenlabs",
}


def selected_provider() -> str:
    raw = (os.environ.get("STT_PROVIDER") or "local_whisper").strip().lower() or "local_whisper"
    return _PROVIDER_ALIASES.get(raw, raw)


def selected_model(provider: str) -> str:
    normalized = _PROVIDER_ALIASES.get(provider.strip().lower(), provider.strip().lower())
    if normalized == "elevenlabs":
        return (os.environ.get("STT_MODEL") or DEFAULT_ELEVENLABS_MODEL).strip() or DEFAULT_ELEVENLABS_MODEL
    return (os.environ.get("LOCAL_WHISPER_MODEL") or os.environ.get("STT_MODEL") or DEFAULT_LOCAL_MODEL).strip() or DEFAULT_LOCAL_MODEL


def selected_audio_preprocessor() -> str:
    raw = (os.environ.get("STT_AUDIO_PREPROCESSOR") or "none").strip().lower() or "none"
    if raw in {"off", "disabled", "false", "0"}:
        return "none"
    if raw in {"elevenlabs", "elevenlabs_isolation", "elevenlabs_audio_isolation", "audio_isolation"}:
        return "elevenlabs_audio_isolation"
    return raw


def normalize_words(raw_words: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_words, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_words:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("word") or "").strip()
        if not text:
            continue
        start = item.get("start")
        end = item.get("end")
        if start is None:
            start = item.get("start_time")
        if end is None:
            end = item.get("end_time")
        rows.append(
            {
                "text": text,
                "start": float(start or 0),
                "end": float(end or 0),
                "type": str(item.get("type") or "word"),
            }
        )
    return rows


def normalize_elevenlabs_response(data: dict[str, Any], *, model: str) -> dict[str, Any]:
    words = data.get("words") or []
    language_code = data.get("language_code") or data.get("language") or ""
    return {
        "transcript": str(data.get("text") or data.get("transcript") or "").strip(),
        "words": normalize_words(words),
        "language_code": str(language_code),
        "model": str(data.get("model") or model),
        "provider": "elevenlabs",
    }


async def transcribe_audio(client: httpx.AsyncClient, audio_path: str) -> dict[str, Any]:
    provider = selected_provider()
    preprocessing = await preprocess_audio(client, audio_path)
    transcription_audio_path = str(preprocessing["audio_path"])
    try:
        payload = await _transcribe_with_provider(client, transcription_audio_path, provider)
    except Exception:
        fallback = (os.environ.get("STT_FALLBACK_PROVIDER") or "").strip().lower()
        fallback = _PROVIDER_ALIASES.get(fallback, fallback)
        if not fallback or fallback == provider:
            raise
        payload = await _transcribe_with_provider(client, transcription_audio_path, fallback)
        payload["fallback_from_provider"] = provider
    payload.update(
        {
            "audio_preprocessor": preprocessing.get("audio_preprocessor"),
            "source_audio_artifact": preprocessing.get("source_audio_artifact"),
        }
    )
    if preprocessing.get("processed_audio_artifact"):
        payload["processed_audio_artifact"] = preprocessing.get("processed_audio_artifact")
    return payload


async def preprocess_audio(client: httpx.AsyncClient, audio_path: str) -> dict[str, Any]:
    preprocessor = selected_audio_preprocessor()
    if preprocessor == "none":
        return {
            "audio_path": audio_path,
            "audio_preprocessor": "none",
            "source_audio_artifact": audio_path,
        }
    if preprocessor == "elevenlabs_audio_isolation":
        return await isolate_audio_elevenlabs(client, audio_path)
    raise RuntimeError(f"unsupported STT_AUDIO_PREPROCESSOR: {preprocessor}")


async def isolate_audio_elevenlabs(client: httpx.AsyncClient, audio_path: str) -> dict[str, Any]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is required when STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation")

    path = Path(audio_path)
    if not path.is_file():
        raise RuntimeError(f"audio file not found for ElevenLabs audio isolation: {path.name}")

    endpoint = os.environ.get("ELEVENLABS_AUDIO_ISOLATION_URL", DEFAULT_ELEVENLABS_AUDIO_ISOLATION_URL)
    output_path = path.with_name(f"{path.stem}.isolated.wav")
    with path.open("rb") as audio_file:
        try:
            response = await client.post(
                endpoint,
                headers={"xi-api-key": api_key},
                files={"file": (path.name, audio_file, "application/octet-stream")},
                timeout=300.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            body = exc.response.text[:500] if exc.response is not None else ""
            if api_key:
                body = body.replace(api_key, "[redacted]")
            raise RuntimeError(f"ElevenLabs audio isolation request failed with status {status}: {body}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"ElevenLabs audio isolation request failed: {type(exc).__name__}") from exc

    output_path.write_bytes(response.content)
    return {
        "audio_path": str(output_path),
        "audio_preprocessor": "elevenlabs_audio_isolation",
        "source_audio_artifact": str(path),
        "processed_audio_artifact": str(output_path),
    }


async def _transcribe_with_provider(client: httpx.AsyncClient, audio_path: str, provider: str) -> dict[str, Any]:
    if provider == "elevenlabs":
        return await transcribe_elevenlabs(client, audio_path, model=selected_model(provider))
    if provider == "local_whisper":
        return await transcribe_local_whisper(client, audio_path, model=selected_model(provider))
    raise RuntimeError(f"unsupported STT_PROVIDER: {provider}")


async def transcribe_local_whisper(client: httpx.AsyncClient, audio_path: str, *, model: str) -> dict[str, Any]:
    base_url = (os.environ.get("STT_HTTP_URL") or DEFAULT_LOCAL_STT_URL).rstrip("/")
    response = await client.post(
        f"{base_url}/transcribe",
        json={"audio_path": audio_path, "model": model},
        timeout=180.0,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "transcript": str(data.get("transcript") or "").strip(),
        "words": normalize_words(data.get("words")),
        "language_code": str(data.get("language_code") or data.get("language") or ""),
        "model": str(data.get("model") or model),
        "provider": "local_whisper",
    }


async def transcribe_elevenlabs(client: httpx.AsyncClient, audio_path: str, *, model: str) -> dict[str, Any]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is required when STT_PROVIDER=elevenlabs")

    path = Path(audio_path)
    if not path.is_file():
        raise RuntimeError(f"audio file not found for ElevenLabs STT: {path.name}")

    endpoint = os.environ.get("ELEVENLABS_STT_URL", DEFAULT_ELEVENLABS_STT_URL)
    with path.open("rb") as audio_file:
        try:
            response = await client.post(
                endpoint,
                headers={"xi-api-key": api_key},
                data={"model_id": model},
                files={"file": (path.name, audio_file, "application/octet-stream")},
                timeout=300.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            body = exc.response.text[:500] if exc.response is not None else ""
            if api_key:
                body = body.replace(api_key, "[redacted]")
            raise RuntimeError(f"ElevenLabs STT request failed with status {status}: {body}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"ElevenLabs STT request failed: {type(exc).__name__}") from exc

    return normalize_elevenlabs_response(response.json(), model=model)
