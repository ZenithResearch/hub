from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import httpx

from libs.tools.local_whisper import tool


class LocalWhisperToolTests(unittest.TestCase):
    def test_posts_transcription_request_to_configured_stt_service(self) -> None:
        response = Mock()
        response.json.return_value = {
            "transcript": "hello world",
            "words": [{"text": "hello", "start": 0.0, "end": 0.4, "type": "word"}],
            "language_code": "en",
            "model": "tiny",
        }
        response.raise_for_status.return_value = None

        with patch.dict(os.environ, {"STT_HTTP_URL": "http://stt.local:8765"}, clear=False), \
             patch.object(tool.httpx, "post", return_value=response) as post:
            result = tool.run({"audio_path": "/data/reviews/assets/review.wav", "language": "en", "model": "base"})

        post.assert_called_once_with(
            "http://stt.local:8765/transcribe",
            json={"audio_path": "/data/reviews/assets/review.wav", "language": "en", "model": "base"},
            timeout=300.0,
        )
        self.assertEqual(result["transcript"], "hello world")
        self.assertEqual(result["words"][0]["text"], "hello")
        self.assertEqual(result["language_code"], "en")
        self.assertEqual(result["model"], "tiny")

    def test_uses_default_stt_service_url_and_tiny_model(self) -> None:
        response = Mock()
        response.json.return_value = {"transcript": "ok", "words": [], "language_code": "", "model": "tiny"}
        response.raise_for_status.return_value = None

        with patch.dict(os.environ, {}, clear=True), patch.object(tool.httpx, "post", return_value=response) as post:
            result = tool.run({"audio_path": "/data/reviews/assets/review.wav"})

        post.assert_called_once_with(
            "http://stt-http:8765/transcribe",
            json={"audio_path": "/data/reviews/assets/review.wav", "model": "tiny"},
            timeout=300.0,
        )
        self.assertEqual(result["transcript"], "ok")

    def test_unavailable_stt_service_raises_clear_error(self) -> None:
        with patch.object(tool.httpx, "post", side_effect=httpx.ConnectError("connection refused")):
            with self.assertRaisesRegex(RuntimeError, "local STT service unavailable"):
                tool.run({"audio_path": "/data/reviews/assets/review.wav"})


if __name__ == "__main__":
    unittest.main()
