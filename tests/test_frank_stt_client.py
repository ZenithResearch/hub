from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx


class FrankSttClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_to_local_whisper_provider(self) -> None:
        from services.frank import stt_client

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(stt_client.selected_provider(), "local_whisper")

    async def test_reads_elevenlabs_provider_from_env(self) -> None:
        from services.frank import stt_client

        with patch.dict(os.environ, {"STT_PROVIDER": "elevenlabs"}, clear=True):
            self.assertEqual(stt_client.selected_provider(), "elevenlabs")
            self.assertEqual(stt_client.selected_model("elevenlabs"), "scribe_v2")

    async def test_normalizes_elevenlabs_scribe_response(self) -> None:
        from services.frank import stt_client

        payload = {
            "text": "hello world",
            "language_code": "en",
            "words": [
                {"text": "hello", "start": 0.1, "end": 0.3, "type": "word"},
                {"word": "world", "start_time": 0.31, "end_time": 0.7},
            ],
        }

        result = stt_client.normalize_elevenlabs_response(payload, model="scribe_v2")

        self.assertEqual(result["transcript"], "hello world")
        self.assertEqual(result["language_code"], "en")
        self.assertEqual(result["model"], "scribe_v2")
        self.assertEqual(result["provider"], "elevenlabs")
        self.assertEqual(result["words"][1], {"text": "world", "start": 0.31, "end": 0.7, "type": "word"})

    async def test_elevenlabs_requires_api_key_only_when_selected(self) -> None:
        from services.frank import stt_client

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "review.webm"
            audio_path.write_bytes(b"fake audio")
            async with httpx.AsyncClient() as client:
                with patch.dict(os.environ, {"STT_PROVIDER": "elevenlabs"}, clear=True):
                    with self.assertRaisesRegex(RuntimeError, "ELEVENLABS_API_KEY is required"):
                        await stt_client.transcribe_audio(client, str(audio_path))

    async def test_local_provider_posts_to_configured_stt_service(self) -> None:
        from services.frank import stt_client

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def post(self, url, json=None, timeout=None, **kwargs):
                self.calls.append((url, json, timeout, kwargs))
                response = Mock()
                response.json.return_value = {
                    "transcript": "local ok",
                    "words": [{"text": "local", "start": 0.0, "end": 0.4}],
                    "language_code": "en",
                    "model": "small",
                }
                response.raise_for_status.return_value = None
                return response

        fake = FakeClient()
        with patch.dict(
            os.environ,
            {"STT_PROVIDER": "local_whisper", "STT_HTTP_URL": "http://stt.local:8765", "STT_MODEL": "small"},
            clear=True,
        ):
            result = await stt_client.transcribe_audio(fake, "/data/frank_execution/review.webm")

        self.assertEqual(fake.calls[0][0], "http://stt.local:8765/transcribe")
        self.assertEqual(fake.calls[0][1], {"audio_path": "/data/frank_execution/review.webm", "model": "small"})
        self.assertEqual(result["provider"], "local_whisper")
        self.assertEqual(result["model"], "small")

    async def test_falls_back_to_local_whisper_when_primary_provider_fails(self) -> None:
        from services.frank import stt_client

        calls = []

        async def fail_elevenlabs(client, audio_path, *, model):
            calls.append(("elevenlabs", model))
            raise RuntimeError("provider failed")

        async def succeed_local(client, audio_path, *, model):
            calls.append(("local_whisper", model))
            return {"transcript": "fallback ok", "words": [], "language_code": "en", "model": model, "provider": "local_whisper"}

        with patch.dict(
            os.environ,
            {"STT_PROVIDER": "elevenlabs", "STT_MODEL": "scribe_v2", "STT_FALLBACK_PROVIDER": "local_whisper", "LOCAL_WHISPER_MODEL": "small"},
            clear=True,
        ), patch.object(stt_client, "transcribe_elevenlabs", side_effect=fail_elevenlabs), patch.object(
            stt_client, "transcribe_local_whisper", side_effect=succeed_local
        ):
            async with httpx.AsyncClient() as client:
                result = await stt_client.transcribe_audio(client, "/tmp/review.webm")

        self.assertEqual(result["transcript"], "fallback ok")
        self.assertEqual(result["provider"], "local_whisper")
        self.assertEqual(calls, [("elevenlabs", "scribe_v2"), ("local_whisper", "small")])

    async def test_elevenlabs_request_uses_scribe_model_and_masks_secret_on_error(self) -> None:
        from services.frank import stt_client

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def post(self, url, headers=None, data=None, files=None, timeout=None, **kwargs):
                self.calls.append((url, headers, data, files, timeout))
                request = httpx.Request("POST", url)
                response = httpx.Response(401, request=request, text="bad secret abc123")
                raise httpx.HTTPStatusError("bad", request=request, response=response)

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "review.webm"
            audio_path.write_bytes(b"fake audio")
            fake = FakeClient()
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "abc123", "STT_MODEL": "scribe_v2"}, clear=True):
                with self.assertRaises(RuntimeError) as raised:
                    await stt_client.transcribe_elevenlabs(fake, str(audio_path), model="scribe_v2")

        self.assertNotIn("abc123", str(raised.exception))
        self.assertEqual(fake.calls[0][2]["model_id"], "scribe_v2")
        self.assertEqual(fake.calls[0][1]["xi-api-key"], "abc123")

    async def test_audio_preprocessor_defaults_to_none(self) -> None:
        from services.frank import stt_client

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(stt_client.selected_audio_preprocessor(), "none")

    async def test_none_audio_preprocessor_returns_original_audio_metadata(self) -> None:
        from services.frank import stt_client

        with patch.dict(os.environ, {"STT_AUDIO_PREPROCESSOR": "none"}, clear=True):
            result = await stt_client.preprocess_audio(Mock(), "/data/frank_execution/review.webm")

        self.assertEqual(result["audio_path"], "/data/frank_execution/review.webm")
        self.assertEqual(result["audio_preprocessor"], "none")
        self.assertEqual(result["source_audio_artifact"], "/data/frank_execution/review.webm")
        self.assertNotIn("processed_audio_artifact", result)

    async def test_elevenlabs_audio_isolation_preprocesses_before_transcription(self) -> None:
        from services.frank import stt_client

        calls = []

        async def fake_isolate(client, audio_path):
            calls.append(("isolate", audio_path))
            return {
                "audio_path": "/tmp/isolated.wav",
                "audio_preprocessor": "elevenlabs_audio_isolation",
                "source_audio_artifact": audio_path,
                "processed_audio_artifact": "/tmp/isolated.wav",
            }

        async def fake_transcribe(client, audio_path, provider):
            calls.append(("transcribe", provider, audio_path))
            return {"transcript": "isolated ok", "words": [], "language_code": "en", "model": "scribe_v2", "provider": provider}

        with patch.dict(os.environ, {"STT_PROVIDER": "elevenlabs", "STT_AUDIO_PREPROCESSOR": "elevenlabs_audio_isolation"}, clear=True), patch.object(
            stt_client, "isolate_audio_elevenlabs", side_effect=fake_isolate
        ), patch.object(stt_client, "_transcribe_with_provider", side_effect=fake_transcribe):
            result = await stt_client.transcribe_audio(Mock(), "/tmp/review.webm")

        self.assertEqual(calls, [("isolate", "/tmp/review.webm"), ("transcribe", "elevenlabs", "/tmp/isolated.wav")])
        self.assertEqual(result["audio_preprocessor"], "elevenlabs_audio_isolation")
        self.assertEqual(result["source_audio_artifact"], "/tmp/review.webm")
        self.assertEqual(result["processed_audio_artifact"], "/tmp/isolated.wav")

    async def test_elevenlabs_audio_isolation_request_uses_dedicated_endpoint_and_masks_secret(self) -> None:
        from services.frank import stt_client

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def post(self, url, headers=None, files=None, timeout=None, **kwargs):
                self.calls.append((url, headers, files, timeout))
                request = httpx.Request("POST", url)
                response = httpx.Response(500, request=request, text="bad secret iso-secret")
                raise httpx.HTTPStatusError("bad", request=request, response=response)

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "review.webm"
            audio_path.write_bytes(b"fake audio")
            fake = FakeClient()
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "iso-secret"}, clear=True):
                with self.assertRaises(RuntimeError) as raised:
                    await stt_client.isolate_audio_elevenlabs(fake, str(audio_path))

        self.assertNotIn("iso-secret", str(raised.exception))
        self.assertEqual(fake.calls[0][0], "https://api.elevenlabs.io/v1/audio-isolation")
        self.assertEqual(fake.calls[0][1]["xi-api-key"], "iso-secret")



if __name__ == "__main__":
    unittest.main()
