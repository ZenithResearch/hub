from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from services.stt_http import main


class SttHttpServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        if hasattr(main, "_MODEL_CACHE"):
            main._MODEL_CACHE.clear()

    def test_request_model_must_be_allowlisted(self) -> None:
        with patch.dict(os.environ, {"STT_ALLOWED_WHISPER_MODELS": "tiny,base"}, clear=False):
            with self.assertRaises(HTTPException) as raised:
                main._model_name("large")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("not allowed", str(raised.exception.detail))

    def test_request_model_uses_allowlisted_env_default(self) -> None:
        with patch.dict(os.environ, {"STT_ALLOWED_WHISPER_MODELS": "tiny,base", "STT_WHISPER_MODEL": "base"}, clear=False):
            self.assertEqual(main._model_name(None), "base")

    def test_transcribe_reuses_loaded_whisper_model_for_same_model_name(self) -> None:
        load_calls: list[str] = []

        class FakeModel:
            def transcribe(self, audio_path: str, **kwargs):
                return {
                    "text": "hello world",
                    "language": "en",
                    "segments": [{"text": "hello world", "start": 0, "end": 1}],
                }

        fake_whisper = types.SimpleNamespace(load_model=lambda name: load_calls.append(name) or FakeModel())

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.webm")
            with open(audio_path, "wb") as handle:
                handle.write(b"fake audio")

            with patch.dict(sys.modules, {"whisper": fake_whisper}), patch.dict(
                os.environ,
                {
                    "STT_ALLOWED_AUDIO_ROOTS": tmpdir,
                    "STT_ALLOWED_WHISPER_MODELS": "tiny,base",
                    "STT_WHISPER_MODEL": "tiny",
                },
                clear=False,
            ):
                first = main.transcribe(main.TranscribeIn(audio_path=audio_path))
                second = main.transcribe(main.TranscribeIn(audio_path=audio_path))

        self.assertEqual(first["transcript"], "hello world")
        self.assertEqual(second["model"], "tiny")
        self.assertEqual(load_calls, ["tiny"])


if __name__ == "__main__":
    unittest.main()
