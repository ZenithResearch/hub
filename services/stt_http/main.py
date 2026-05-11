from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DEFAULT_MODEL = "tiny"
DEFAULT_ALLOWED_AUDIO_ROOTS = "/data/reviews"
DEFAULT_ALLOWED_MODELS = "tiny,base,small"

app = FastAPI(title="Hub Local STT HTTP", version="0.1.0")


class TranscribeIn(BaseModel):
    audio_path: str
    language: str | None = None
    model: str | None = None


class TranscribeOut(BaseModel):
    transcript: str
    words: list[dict[str, Any]]
    language_code: str
    model: str


def _allowed_roots() -> list[Path]:
    raw = os.environ.get("STT_ALLOWED_AUDIO_ROOTS", DEFAULT_ALLOWED_AUDIO_ROOTS)
    roots: list[Path] = []
    for part in raw.split(os.pathsep):
        text = part.strip()
        if text:
            roots.append(Path(text).expanduser().resolve())
    return roots


def _allowed_models() -> set[str]:
    raw = os.environ.get("STT_ALLOWED_WHISPER_MODELS", DEFAULT_ALLOWED_MODELS)
    return {part.strip() for part in raw.split(",") if part.strip()}


def _model_name(requested: str | None) -> str:
    model_name = (requested or os.environ.get("STT_WHISPER_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    allowed = _allowed_models()
    if not allowed:
        raise HTTPException(status_code=500, detail="STT_ALLOWED_WHISPER_MODELS is empty")
    if model_name not in allowed:
        raise HTTPException(status_code=400, detail=f"Whisper model is not allowed: {model_name}")
    return model_name


def _validate_audio_path(raw_path: str) -> Path:
    try:
        audio_path = Path(raw_path).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"audio file not found: {raw_path}") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"invalid audio path: {raw_path}") from exc

    allowed_roots = _allowed_roots()
    if not allowed_roots:
        raise HTTPException(status_code=500, detail="STT_ALLOWED_AUDIO_ROOTS is empty")

    if not any(audio_path == root or root in audio_path.parents for root in allowed_roots):
        allowed = ", ".join(str(root) for root in allowed_roots)
        raise HTTPException(status_code=403, detail=f"audio path is outside allowed roots: {allowed}")

    if not audio_path.is_file():
        raise HTTPException(status_code=400, detail=f"audio path is not a file: {raw_path}")

    return audio_path


def _segment_words(segment: dict[str, Any]) -> list[dict[str, Any]]:
    words = segment.get("words") or []
    if words:
        return [
            {
                "text": str(word.get("word") or word.get("text") or "").strip(),
                "start": float(word.get("start") or 0),
                "end": float(word.get("end") or 0),
                "type": "word",
            }
            for word in words
            if str(word.get("word") or word.get("text") or "").strip()
        ]

    text = str(segment.get("text") or "").strip()
    if not text:
        return []
    return [
        {
            "text": text,
            "start": float(segment.get("start") or 0),
            "end": float(segment.get("end") or 0),
            "type": "segment",
        }
    ]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/transcribe", response_model=TranscribeOut)
def transcribe(payload: TranscribeIn) -> dict[str, Any]:
    audio_path = _validate_audio_path(payload.audio_path)
    model_name = _model_name(payload.model)

    try:
        import whisper  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - only expected in broken images
        raise HTTPException(status_code=500, detail="openai-whisper is not installed in the STT service") from exc

    try:
        model = whisper.load_model(model_name)
        kwargs: dict[str, Any] = {"verbose": False, "word_timestamps": True}
        if payload.language:
            kwargs["language"] = payload.language
        try:
            result = model.transcribe(str(audio_path), **kwargs)
        except TypeError:
            kwargs.pop("word_timestamps", None)
            result = model.transcribe(str(audio_path), **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"local Whisper transcription failed: {exc}") from exc

    timing_rows: list[dict[str, Any]] = []
    for segment in list(result.get("segments") or []):
        timing_rows.extend(_segment_words(segment))

    return {
        "transcript": str(result.get("text") or "").strip(),
        "words": timing_rows,
        "language_code": str(result.get("language") or payload.language or ""),
        "model": model_name,
    }
