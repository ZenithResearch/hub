from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class GatewayHttpSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        reviews_dir = root / "reviews"
        reviews_dir.mkdir(parents=True)
        hermes_dir = root / ".hermes"
        hermes_dir.mkdir(parents=True)
        session_payload = {
            "session_id": "20260501_010203_abcdef",
            "messages": [
                {"role": "user", "content": "do thing with token sk_raw_secret"},
                {"role": "assistant", "content": {"authorization": "Bearer raw-token", "payload": "full model payload"}},
            ],
        }
        (hermes_dir / "session_20260501_010203_abcdef.json").write_text(
            json.dumps(session_payload),
            encoding="utf-8",
        )

        os.environ["REVIEWS_DATA_DIR"] = str(reviews_dir)
        os.environ["QUEUE_HTTP_URL"] = "http://queue:8081"
        os.environ["EVENTBUS_URL"] = "http://eventbus:8082"
        os.environ["RUNTIME_GRPC_TARGET"] = "runtime-grpc:50051"
        os.environ["HERMES_SESSION_ROOTS"] = str(hermes_dir)
        os.environ["HUB_CONFIG_SECRETS_PATH"] = str(root / "config-secrets.env")

        async def _close() -> None:
            return None

        self._orig_grpc = sys.modules.get("grpc")
        self._orig_agent_pb2 = sys.modules.get("libs.common.proto.agent_pb2")
        self._orig_agent_pb2_grpc = sys.modules.get("libs.common.proto.agent_pb2_grpc")

        fake_grpc = types.SimpleNamespace()
        fake_grpc.aio = types.SimpleNamespace(
            insecure_channel=lambda target: types.SimpleNamespace(close=_close)
        )
        sys.modules["grpc"] = fake_grpc
        stub_module = types.SimpleNamespace(AgentRuntimeStub=lambda channel: object())
        sys.modules["libs.common.proto.agent_pb2_grpc"] = stub_module
        sys.modules["libs.common.proto.agent_pb2"] = types.SimpleNamespace()

        sys.modules.pop("services.gateway_http.app", None)
        module = importlib.import_module("services.gateway_http.app")
        self.module = importlib.reload(module)
        self.client_context = TestClient(self.module.app)
        self.client = self.client_context.__enter__()
        self.remote_client_context = TestClient(
            self.module.app,
            client=("203.0.113.10", 50000),
        )
        self.remote_client = self.remote_client_context.__enter__()

    def tearDown(self) -> None:
        self.remote_client_context.__exit__(None, None, None)
        self.client_context.__exit__(None, None, None)
        if self._orig_grpc is not None:
            sys.modules["grpc"] = self._orig_grpc
        else:
            sys.modules.pop("grpc", None)
        if self._orig_agent_pb2 is not None:
            sys.modules["libs.common.proto.agent_pb2"] = self._orig_agent_pb2
        else:
            sys.modules.pop("libs.common.proto.agent_pb2", None)
        if self._orig_agent_pb2_grpc is not None:
            sys.modules["libs.common.proto.agent_pb2_grpc"] = self._orig_agent_pb2_grpc
        else:
            sys.modules.pop("libs.common.proto.agent_pb2_grpc", None)
        self.tmpdir.cleanup()

    def _write_review_asset_meta(self, asset_id: str, asset_type: str, *, mime_type: str = "application/json") -> None:
        reviews_dir = Path(os.environ["REVIEWS_DATA_DIR"])
        assets_dir = reviews_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / asset_id).write_text("{}", encoding="utf-8")
        (assets_dir / f"{asset_id}.meta.json").write_text(
            json.dumps(
                {
                    "asset_id": asset_id,
                    "asset_type": asset_type,
                    "mime_type": mime_type,
                    "size_bytes": 2,
                }
            ),
            encoding="utf-8",
        )

    def test_submit_review_requires_typed_audio_and_events_assets(self) -> None:
        response = self.client.post(
            "/v1/reviews",
            json={
                "review_id": "review-typed-required",
                "subject_id": "http://example",
                "submitted_by": "tester",
                "started_at": "2026-05-01T00:00:00Z",
                "stopped_at": "2026-05-01T00:00:10Z",
                "duration_ms": 10000,
                "asset_ids": [],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("events_asset_id", response.text)

    def test_submit_review_rejects_swapped_typed_asset_ids(self) -> None:
        self._write_review_asset_meta("events-1", "events")
        self._write_review_asset_meta("audio-1", "audio", mime_type="audio/webm")

        response = self.client.post(
            "/v1/reviews",
            json={
                "review_id": "review-swapped-assets",
                "subject_id": "http://example",
                "submitted_by": "tester",
                "started_at": "2026-05-01T00:00:00Z",
                "stopped_at": "2026-05-01T00:00:10Z",
                "duration_ms": 10000,
                "events_asset_id": "audio-1",
                "audio_asset_id": "events-1",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("events_asset_id must reference a events asset", response.text)
        self.assertFalse((Path(os.environ["REVIEWS_DATA_DIR"]) / "review-swapped-assets.json").exists())

    def test_submit_review_enqueues_without_process_path_and_records_typed_assets(self) -> None:
        reviews_dir = Path(os.environ["REVIEWS_DATA_DIR"])
        self._write_review_asset_meta("events-1", "events")
        self._write_review_asset_meta("audio-1", "audio", mime_type="audio/webm")

        posted: list[tuple[str, dict]] = []

        class FakeResponse:
            def __init__(self, payload: dict):
                self._payload = payload

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return self._payload

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url: str, json: dict):
                posted.append((url, json))
                return FakeResponse({"id": "msg-review-1"})

        with patch.object(self.module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.post(
                "/v1/reviews",
                json={
                    "review_id": "review-typed-assets",
                    "subject_id": "http://example",
                    "submitted_by": "tester",
                    "started_at": "2026-05-01T00:00:00Z",
                    "stopped_at": "2026-05-01T00:00:10Z",
                    "duration_ms": 10000,
                    "asset_ids": [],
                    "events_asset_id": "events-1",
                    "audio_asset_id": "audio-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        saved = json.loads((reviews_dir / "review-typed-assets.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["events_asset_id"], "events-1")
        self.assertEqual(saved["audio_asset_id"], "audio-1")
        enqueue_payload = next(payload for url, payload in posted if url.endswith("/queues/workspace/enqueue"))
        self.assertNotIn("process_path", enqueue_payload)
        self.assertEqual(enqueue_payload["payload"]["events_asset_id"], "events-1")

    def test_rerun_case_reenqueues_without_process_path(self) -> None:
        posted: list[tuple[str, dict]] = []

        class FakeResponse:
            def __init__(self, payload: dict, status_code: int = 200):
                self._payload = payload
                self.status_code = status_code

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return self._payload

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url: str):
                if url.endswith("/cases/case_123"):
                    return FakeResponse(
                        {
                            "case": {
                                "id": "case_123",
                                "status": "COMPLETED",
                                "queue_message_id": "msg_123",
                                "process_path": "process-queued-review",
                            }
                        }
                    )
                if url.endswith("/messages/msg_123"):
                    return FakeResponse(
                        {
                            "event_type": "review_submitted",
                            "source_type": "review_sdk",
                            "sender": "tester",
                            "message_body": "review_123",
                            "payload": {"review_id": "review_123"},
                        }
                    )
                return FakeResponse({}, status_code=404)

            async def post(self, url: str, json: dict, timeout: float | None = None):
                posted.append((url, json))
                return FakeResponse({"id": "msg_new"})

        with patch.object(self.module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.post("/v1/cases/case_123/rerun", params={"force": True})

        self.assertEqual(response.status_code, 200)
        enqueue_payload = next(payload for url, payload in posted if url.endswith("/queues/workspace/enqueue"))
        self.assertNotIn("process_path", enqueue_payload)
        self.assertEqual(enqueue_payload["payload"], {"review_id": "review_123"})

    def test_get_session_returns_safe_summary(self) -> None:
        response = self.client.get("/v1/hermes/sessions/20260501_010203_abcdef")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], "20260501_010203_abcdef")
        self.assertEqual(payload["message_count"], 2)
        dumped = json.dumps(payload)
        self.assertNotIn("sk_raw_secret", dumped)
        self.assertNotIn("Bearer raw-token", dumped)
        self.assertNotIn("full model payload", dumped)
        self.assertNotIn("content", payload["messages"][0])

    def test_get_session_messages_returns_safe_message_summaries(self) -> None:
        response = self.client.get("/v1/hermes/sessions/20260501_010203_abcdef/messages")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], "20260501_010203_abcdef")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["role"], "user")
        dumped = json.dumps(payload)
        self.assertNotIn("sk_raw_secret", dumped)
        self.assertNotIn("authorization", dumped)
        self.assertNotIn("full model payload", dumped)

    def test_get_session_rejects_invalid_session_id(self) -> None:
        response = self.client.get("/v1/hermes/sessions/bad%3Cscript%3E")
        self.assertEqual(response.status_code, 422)

    def test_admin_config_masks_allowlisted_stt_secret(self) -> None:
        put_response = self.client.put(
            "/v1/admin/config/secrets/ELEVENLABS_API_KEY",
            json={"value": "sk_tes...cdef"},
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertTrue(put_response.json()["configured"])
        self.assertNotIn("sk_test", json.dumps(put_response.json()))

        get_response = self.client.get("/v1/admin/config")
        self.assertEqual(get_response.status_code, 200)
        payload = get_response.json()
        secret = payload["secrets"]["ELEVENLABS_API_KEY"]
        self.assertTrue(secret["configured"])
        self.assertEqual(secret["preview"], "sk_t...cdef")
        self.assertNotIn("sk_tes...cdef", json.dumps(payload))

    def test_admin_config_rejects_unallowlisted_secret(self) -> None:
        response = self.client.put(
            "/v1/admin/config/secrets/OPENROUTER_API_KEY",
            json={"value": "secret"},
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_config_rejects_secret_value_newline_injection(self) -> None:
        for value in ("sk_allowed\nNOT_ALLOWLISTED=VALUE", "sk_allowed\n", "\rsk_allowed"):
            response = self.client.put(
                "/v1/admin/config/secrets/ELEVENLABS_API_KEY",
                json={"value": value},
            )
            self.assertEqual(response.status_code, 422)
        self.assertFalse(Path(os.environ["HUB_CONFIG_SECRETS_PATH"]).exists())

    def test_gateway_compose_binds_http_to_localhost_by_default(self) -> None:
        compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(
            '"${HTTP_BIND_HOST:-127.0.0.1}:${HTTP_PORT:-8080}:${HTTP_PORT:-8080}"',
            compose,
        )
        self.assertNotIn('"${HTTP_PORT:-8080}:${HTTP_PORT:-8080}"', compose)

    def test_patch_review_status_updates_record_atomically(self) -> None:
        reviews_dir = Path(os.environ["REVIEWS_DATA_DIR"])
        review_path = reviews_dir / "review-123.json"
        review_path.write_text(json.dumps({"review_id": "review-123", "status": "queued", "subject_id": "http://example"}))

        response = self.client.patch(
            "/v1/reviews/review-123/status",
            json={"status": "processed", "review_note_path": "~/claude-hub/notes/review review-123.md"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "processed")

        saved = json.loads(review_path.read_text())
        self.assertEqual(saved["status"], "processed")
        self.assertEqual(saved["review_note_path"], "~/claude-hub/notes/review review-123.md")
        self.assertEqual(saved["subject_id"], "http://example")
