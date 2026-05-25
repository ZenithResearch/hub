from __future__ import annotations

import importlib
import json
import os
import sqlite3
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
        os.environ.pop("REVIEW_AUTH_DB_PATH", None)
        os.environ.pop("CLIENTS_DB_BACKEND", None)
        os.environ.pop("CLIENTS_DATABASE_URL", None)
        os.environ["CLIENTS_DB_PATH"] = str(root / "clients.db")
        os.environ["REVIEW_SESSION_TTL_SECONDS"] = "3600"
        os.environ["REVIEW_ACCESS_ADMIN_TOKEN"] = "admin-secret"
        os.environ.pop("REVIEW_DEPLOY_HOOK_TOKEN", None)
        os.environ.pop("REVIEW_DEPLOY_ALLOWED_HOST_SUFFIXES", None)
        os.environ["QUEUE_HTTP_URL"] = "http://queue:8081"
        os.environ["CASES_HTTP_URL"] = "http://cases:8083"
        os.environ["EVENTBUS_URL"] = "http://eventbus:8082"
        os.environ["CORS_ALLOW_ORIGINS"] = "https://staging.example.com,https://swrl-ui.vercel.app"
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
        self.review_auth_module = importlib.import_module("services.gateway_http.review_auth")
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

    def _seed_review_auth(
        self,
        *,
        client_id: str = "client-1",
        project_id: str = "project-1",
        project_slug: str = "project-one",
        deployment_id: str = "deployment-1",
        deployment_slug: str = "deployment-one",
        origin: str = "https://staging.example.com",
        code: str = "let-me-review",
        label: str = "Tester",
        subject_pattern: str | None = "https://staging.example.com/*",
        project_scoped_access_code: bool = False,
    ) -> None:
        db_path = os.environ["CLIENTS_DB_PATH"]
        code_hash = self.review_auth_module.hash_access_code(code)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO clients (id, slug, name, rolodex_entry_path, created_at) VALUES (?, ?, ?, ?, ?)",
                (client_id, "client-one", "Client One", "notes/Client One.md", "2026-05-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO projects (id, client_id, slug, name, created_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, client_id, project_slug, "Project One", "2026-05-01T00:00:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO review_deployments
                (id, project_id, slug, branch, allowed_origin, subject_pattern, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (deployment_id, project_id, deployment_slug, "main", origin, subject_pattern, "2026-05-01T00:00:00+00:00"),
            )
            conn.execute(
                """
                INSERT INTO review_access_codes
                (id, project_id, deployment_id, label, email, code_hash, active, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL)
                """,
                ("code-1", project_id, None if project_scoped_access_code else deployment_id, label, "owner@example.com", code_hash, "2026-05-01T00:00:00+00:00"),
            )

    def _seed_deploy_hook(
        self,
        *,
        hook_id: str = "hook-1",
        project_id: str = "project-1",
        secret: str = "deploy-hook-secret",
        allowed_host_suffixes: str = ".vercel.app,localhost",
        active: int = 1,
    ) -> str:
        db_path = os.environ["CLIENTS_DB_PATH"]
        token = f"rdh_{hook_id}_{secret}"
        token_hash = self.review_auth_module.hash_deploy_hook_token(secret)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO review_deploy_hooks
                (id, project_id, label, token_hash, allowed_host_suffixes, active, created_at, expires_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (hook_id, project_id, "Test deploy hook", token_hash, allowed_host_suffixes, active, "2026-05-01T00:00:00+00:00"),
            )
        return token

    def _create_review_session(self, *, project_id: str = "project-one", deployment_id: str = "deployment-one", origin: str = "https://staging.example.com", code: str = "let-me-review", subject_id: str = "https://staging.example.com/page") -> dict:
        response = self.client.post(
            "/v1/review-auth/session",
            headers={"Origin": origin},
            json={
                "project_id": project_id,
                "deployment_id": deployment_id,
                "email": "owner@example.com",
                "access_code": code,
                "subject_id": subject_id,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _auth_headers(self, token: str, origin: str = "https://staging.example.com") -> dict[str, str]:
        return {"Origin": origin, "Authorization": f"Bearer {token}"}

    def _admin_headers(self, token: str = "admin-secret") -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_admin_review_access_capabilities_verifies_admin_token(self) -> None:
        response = self.client.get(
            "/v1/admin/review-auth/capabilities",
            headers=self._admin_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["hub"], "gateway-http")
        self.assertIn("review_access_admin", payload["capabilities"])
        self.assertIn("review_access_rotate", payload["capabilities"])
        self.assertEqual(payload["secrets_printed"], False)

    def test_admin_review_access_capabilities_rejects_invalid_token(self) -> None:
        response = self.client.get(
            "/v1/admin/review-auth/capabilities",
            headers=self._admin_headers("wrong-token"),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "invalid review access admin token")

    def test_admin_review_access_admin_token_update_requires_current_token_when_configured(self) -> None:
        response = self.client.put(
            "/v1/admin/review-auth/admin-token",
            headers=self._admin_headers("wrong-token"),
            json={"value": "new-admin-secret-token-at-least-32-chars"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(Path(os.environ["HUB_CONFIG_SECRETS_PATH"]).exists())

    def test_admin_queue_peek_requires_review_access_admin_token(self) -> None:
        response = self.client.get("/v1/admin/queues/workspace/peek?n=1")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "invalid review access admin token")

    def test_admin_cases_requires_review_access_admin_token(self) -> None:
        response = self.client.get("/v1/admin/cases?limit=1")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "invalid review access admin token")

    def test_admin_queue_peek_proxies_to_queue_service(self) -> None:
        calls: list[tuple[str, dict | None]] = []

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self) -> dict:
                return {"messages": [{"id": "msg-1"}]}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url: str, params: dict | None = None, timeout: float | None = None):
                calls.append((url, params))
                return FakeResponse()

        with patch.object(self.module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.get(
                "/v1/admin/queues/workspace/peek?n=25&status=pending",
                headers=self._admin_headers(),
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["messages"][0]["id"], "msg-1")
        self.assertEqual(calls, [("http://queue:8081/queues/workspace/peek", {"n": "25", "status": "pending"})])

    def test_admin_cases_proxies_to_cases_service(self) -> None:
        calls: list[tuple[str, dict | None]] = []

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self) -> dict:
                return {"cases": []}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url: str, params: dict | None = None, timeout: float | None = None):
                calls.append((url, params))
                return FakeResponse()

        with patch.object(self.module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.get(
                "/v1/admin/cases?status=ACTIVE&limit=10",
                headers=self._admin_headers(),
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"cases": []})
        self.assertEqual(calls, [("http://cases:8083/cases", {"status": "ACTIVE", "limit": "10"})])

    def test_admin_case_detail_preserves_upstream_404(self) -> None:
        class FakeResponse:
            status_code = 404
            headers = {"content-type": "application/json"}

            def json(self) -> dict:
                return {"detail": "case not found"}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url: str, params: dict | None = None, timeout: float | None = None):
                return FakeResponse()

        with patch.object(self.module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.get(
                "/v1/admin/cases/missing-case",
                headers=self._admin_headers(),
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "case not found"})

    def test_admin_review_access_admin_token_update_rotates_effective_token(self) -> None:
        new_token = "new-admin-secret-token-at-least-32-chars"
        response = self.client.put(
            "/v1/admin/review-auth/admin-token",
            headers=self._admin_headers(),
            json={"value": new_token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["configured"])
        self.assertEqual(body["secrets_printed"], False)
        self.assertNotIn(new_token, json.dumps(body))

        old_response = self.client.get(
            "/v1/admin/review-auth/capabilities",
            headers=self._admin_headers(),
        )
        self.assertEqual(old_response.status_code, 401)

        new_response = self.client.get(
            "/v1/admin/review-auth/capabilities",
            headers=self._admin_headers(new_token),
        )
        self.assertEqual(new_response.status_code, 200, new_response.text)

    def test_admin_review_access_admin_token_bootstraps_when_unconfigured(self) -> None:
        self.module.app.state.settings.review_access_admin_token = ""
        new_token = "bootstrap-admin-secret-at-least-32-chars"
        response = self.client.put(
            "/v1/admin/review-auth/admin-token",
            json={"value": new_token},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(new_token, json.dumps(response.json()))

        verify_response = self.client.get(
            "/v1/admin/review-auth/capabilities",
            headers=self._admin_headers(new_token),
        )
        self.assertEqual(verify_response.status_code, 200, verify_response.text)

    def _write_review_asset_meta(self, asset_id: str, asset_type: str, *, mime_type: str = "application/json", session: dict | None = None) -> None:
        reviews_dir = Path(os.environ["REVIEWS_DATA_DIR"])
        assets_dir = reviews_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / asset_id).write_text("{}", encoding="utf-8")
        meta = {
            "asset_id": asset_id,
            "asset_type": asset_type,
            "mime_type": mime_type,
            "size_bytes": 2,
        }
        if session is not None:
            meta.update(
                {
                    "client_id": "client-1",
                    "project_id": session["project_id"],
                    "deployment_id": session["deployment_id"],
                    "auth_session_id": session["session_id"],
                    "authenticated": True,
                    "origin": "https://staging.example.com",
                    "submitted_by": session["label"],
                }
            )
        (assets_dir / f"{asset_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_unauthenticated_asset_upload_is_rejected(self) -> None:
        response = self.client.post(
            "/v1/reviews/assets",
            files={"file": ("events.json", b"{}", "application/json")},
            data={"asset_type": "events", "project_id": "project-one", "deployment_id": "deployment-one"},
            headers={"Origin": "https://staging.example.com"},
        )
        self.assertEqual(response.status_code, 401)

    def test_admin_review_access_rotate_generates_raw_code_once_and_project_scoped_hash(self) -> None:
        response = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers(),
            json={
                "client_id": "dan-prota",
                "client_slug": "dan-prota",
                "client_name": "Dan Prota",
                "rolodex_entry_path": "notes/dan.md",
                "project_id": "swrl-ui",
                "project_slug": "swrl-ui",
                "project_name": "SWRL UI",
                "deployment_id": "swrl-ui-production-alias",
                "deployment_slug": "swrl-ui-production-alias",
                "allowed_origin": "https://swrl-ui.vercel.app",
                "subject_pattern": "https://swrl-ui.vercel.app*",
                "access_code_id": "dan-prota-swrl-ui-review",
                "access_label": "Dan Prota",
                "mode": "generate",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        raw_code = body.get("raw_code")
        self.assertTrue(raw_code)
        self.assertFalse(body.get("secrets_printed"))
        self.assertNotIn("code_hash", body)
        with sqlite3.connect(os.environ["CLIENTS_DB_PATH"]) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT deployment_id, code_hash, active FROM review_access_codes WHERE id = ?",
                ("dan-prota-swrl-ui-review",),
            ).fetchone()
        self.assertIsNone(row["deployment_id"])
        self.assertEqual(row["active"], 1)
        self.assertTrue(self.review_auth_module.verify_access_code(raw_code, row["code_hash"]))

        auth_response = self.client.post(
            "/v1/review-auth/session",
            headers={"Origin": "https://swrl-ui.vercel.app"},
            json={
                "project_id": "swrl-ui",
                "deployment_id": "swrl-ui-production-alias",
                "access_code": raw_code,
                "subject_id": "https://swrl-ui.vercel.app/",
            },
        )
        self.assertEqual(auth_response.status_code, 200, auth_response.text)
        self.assertTrue(auth_response.json().get("token"))

    def test_admin_review_access_rotate_accepts_multiple_access_code_policies(self) -> None:
        response = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers(),
            json={
                "client_id": "luna-lovegood",
                "client_slug": "luna-lovegood",
                "client_name": "Luna Lovegood",
                "project_id": "gallery",
                "project_slug": "gallery",
                "project_name": "Gallery",
                "access_code_id": "luna-lovegood-gallery-review",
                "access_label": "Luna Lovegood",
                "mode": "provided",
                "access_code": "gallery-review-code-for-luna",
                "policies": [
                    {
                        "deployment_id": "gallery-production",
                        "deployment_slug": "gallery-production",
                        "allowed_origin": "https://gal-ler-y.com",
                        "subject_pattern": "https://gal-ler-y.com/*",
                    },
                    {
                        "deployment_id": "gallery-local",
                        "deployment_slug": "gallery-local",
                        "allowed_origin": "http://localhost:3000",
                        "subject_pattern": "http://localhost:3000/*",
                    },
                ],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body.get("raw_code_present"))
        self.assertEqual(body.get("policy_count"), 2)

        prod_auth = self.client.post(
            "/v1/review-auth/session",
            headers={"Origin": "https://gal-ler-y.com"},
            json={
                "project_id": "gallery",
                "deployment_id": "gallery-production",
                "access_code": "gallery-review-code-for-luna",
                "subject_id": "https://gal-ler-y.com/admin/events",
            },
        )
        self.assertEqual(prod_auth.status_code, 200, prod_auth.text)

        local_auth = self.client.post(
            "/v1/review-auth/session",
            headers={"Origin": "http://localhost:3000"},
            json={
                "project_id": "gallery",
                "deployment_id": "gallery-local",
                "access_code": "gallery-review-code-for-luna",
                "subject_id": "http://localhost:3000/admin/events",
            },
        )
        self.assertEqual(local_auth.status_code, 200, local_auth.text)

        rejected_origin = self.client.post(
            "/v1/review-auth/session",
            headers={"Origin": "https://evil.example"},
            json={
                "project_id": "gallery",
                "deployment_id": "gallery-production",
                "access_code": "gallery-review-code-for-luna",
                "subject_id": "https://evil.example/admin/events",
            },
        )
        self.assertEqual(rejected_origin.status_code, 401)

    def test_admin_review_access_rotate_reconciles_glass_bead_policy_sequence(self) -> None:
        with sqlite3.connect(os.environ["CLIENTS_DB_PATH"]) as conn:
            conn.execute(
                "INSERT INTO clients (id, slug, name, created_at) VALUES (?, ?, ?, ?)",
                ("hermione-granger", "hermione-granger", "Hermione Granger", "2026-05-25T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO projects (id, client_id, slug, name, created_at) VALUES (?, ?, ?, ?, ?)",
                ("gallery", "hermione-granger", "gallery", "Gallery", "2026-05-25T00:00:00Z"),
            )
            conn.execute(
                """
                INSERT INTO review_access_codes
                    (id, project_id, deployment_id, label, email, code_hash, active, created_at, expires_at)
                VALUES (?, ?, NULL, ?, NULL, ?, 1, ?, NULL)
                """,
                (
                    "hermione-granger-gallery-review",
                    "gallery",
                    "Hermione Granger",
                    "prior-glass-bead-hash-placeholder",
                    "2026-05-25T00:00:00Z",
                ),
            )
            conn.execute(
                """
                INSERT INTO review_access_code_policies
                    (id, access_code_id, project_id, deployment_id, allowed_origin, subject_pattern, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    "old-bead-sequence-row",
                    "hermione-granger-gallery-review",
                    "gallery",
                    "gallery-production",
                    "https://gal-ler-y.com",
                    "https://gal-ler-y.com/*",
                    "2026-05-25T00:00:00Z",
                    "2026-05-25T00:00:00Z",
                ),
            )

        response = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers(),
            json={
                "client_id": "hermione-granger",
                "client_slug": "hermione-granger",
                "client_name": "Hermione Granger",
                "project_id": "gallery",
                "project_slug": "gallery",
                "project_name": "Gallery",
                "access_code_id": "hermione-granger-gallery-review",
                "access_label": "Hermione Granger",
                "mode": "generate",
                "policies": [
                    {
                        "deployment_id": "gallery-production",
                        "deployment_slug": "gallery-production",
                        "allowed_origin": "https://gal-ler-y.com",
                        "subject_pattern": "https://gal-ler-y.com/*",
                    },
                    {
                        "deployment_id": "gallery-local",
                        "deployment_slug": "gallery-local",
                        "allowed_origin": "http://localhost:3000",
                        "subject_pattern": "http://localhost:3000/*",
                    },
                ],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get("policy_count"), 2)
        with sqlite3.connect(os.environ["CLIENTS_DB_PATH"]) as conn:
            rows = conn.execute(
                "SELECT id, active FROM review_access_code_policies WHERE access_code_id = ? ORDER BY deployment_id",
                ("hermione-granger-gallery-review",),
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotIn("old-bead-sequence-row", {row[0] for row in rows})
        self.assertTrue(all(row[1] == 1 for row in rows))

    def test_admin_review_access_rotate_rejects_gallery_legacy_policy_ids(self) -> None:
        response = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers(),
            json={
                "client_id": "neville-longbottom",
                "client_slug": "neville-longbottom",
                "client_name": "Neville Longbottom",
                "project_id": "gallery",
                "project_slug": "gallery",
                "project_name": "Gallery",
                "deployment_id": "gallery-dev",
                "deployment_slug": "gallery-dev",
                "allowed_origin": "http://localhost:3000",
                "subject_pattern": "http://localhost:3000*",
                "access_code_id": "neville-longbottom-gallery-review",
                "access_label": "Neville Longbottom",
                "mode": "generate",
                "policies": [
                    {
                        "deployment_id": "gallery-dev",
                        "deployment_slug": "gallery-dev",
                        "allowed_origin": "http://localhost:3000",
                        "subject_pattern": "http://localhost:3000*",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("cannot rotate legacy deployment IDs", response.text)

    def test_admin_review_access_rotate_requires_canonical_gallery_policy_pair(self) -> None:
        response = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers(),
            json={
                "client_id": "neville-longbottom",
                "client_slug": "neville-longbottom",
                "client_name": "Neville Longbottom",
                "project_id": "gallery",
                "project_slug": "gallery",
                "project_name": "Gallery",
                "access_code_id": "neville-longbottom-gallery-review",
                "access_label": "Neville Longbottom",
                "mode": "generate",
                "policies": [
                    {
                        "deployment_id": "gallery-local",
                        "deployment_slug": "gallery-local",
                        "allowed_origin": "http://localhost:3000",
                        "subject_pattern": "http://localhost:3000/*",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("requires exactly the canonical", response.text)

    def test_admin_review_access_rotate_provided_code_does_not_echo_raw_code(self) -> None:
        response = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers(),
            json={
                "client_id": "client-1",
                "client_slug": "client-one",
                "client_name": "Client One",
                "project_id": "project-1",
                "project_slug": "project-one",
                "project_name": "Project One",
                "deployment_id": "deployment-1",
                "deployment_slug": "deployment-one",
                "allowed_origin": "https://staging.example.com",
                "subject_pattern": "https://staging.example.com/*",
                "access_code_id": "code-1",
                "access_label": "Tester",
                "mode": "provided",
                "access_code": "manually-chosen-review-code",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("raw_code", body)
        self.assertFalse(body.get("raw_code_present"))
        self.assertFalse(body.get("secrets_printed"))
        auth_response = self.client.post(
            "/v1/review-auth/session",
            headers={"Origin": "https://staging.example.com"},
            json={
                "project_id": "project-one",
                "deployment_id": "deployment-one",
                "access_code": "manually-chosen-review-code",
                "subject_id": "https://staging.example.com/page",
            },
        )
        self.assertEqual(auth_response.status_code, 200, auth_response.text)

    def test_admin_review_access_rotate_requires_admin_bearer_token(self) -> None:
        payload = {
            "client_id": "client-1",
            "client_slug": "client-one",
            "client_name": "Client One",
            "project_id": "project-1",
            "project_slug": "project-one",
            "project_name": "Project One",
            "access_code_id": "code-1",
            "access_label": "Tester",
            "mode": "generate",
        }
        missing = self.client.post("/v1/admin/review-auth/access-codes/rotate", json=payload)
        self.assertEqual(missing.status_code, 401)
        wrong = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers("wrong-token"),
            json=payload,
        )
        self.assertEqual(wrong.status_code, 401)

    def test_admin_review_access_rotate_rejects_partial_deployment_metadata(self) -> None:
        response = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers(),
            json={
                "client_id": "client-1",
                "client_slug": "client-one",
                "client_name": "Client One",
                "project_id": "project-1",
                "project_slug": "project-one",
                "project_name": "Project One",
                "deployment_id": "deployment-1",
                "access_code_id": "code-1",
                "access_label": "Tester",
                "mode": "generate",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("incomplete deployment metadata", response.text)

        scoped_without_deployment = self.client.post(
            "/v1/admin/review-auth/access-codes/rotate",
            headers=self._admin_headers(),
            json={
                "client_id": "client-1",
                "client_slug": "client-one",
                "client_name": "Client One",
                "project_id": "project-1",
                "project_slug": "project-one",
                "project_name": "Project One",
                "access_code_id": "code-1",
                "access_label": "Tester",
                "mode": "generate",
                "deployment_scoped_access": True,
            },
        )
        self.assertEqual(scoped_without_deployment.status_code, 422)
        self.assertIn("deployment_id is required", scoped_without_deployment.text)

    def test_submit_review_requires_authentication_before_payload_validation(self) -> None:
        response = self.client.post(
            "/v1/reviews",
            headers={"Origin": "https://staging.example.com"},
            json={
                "review_id": "review-typed-required",
                "subject_id": "http://example",
                "submitted_by": "tester",
                "started_at": "2026-05-01T00:00:00Z",
                "stopped_at": "2026-05-01T00:00:10Z",
                "duration_ms": 10000,
                "project_id": "project-one",
                "deployment_id": "deployment-one",
                "asset_ids": [],
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("review authentication required", response.text)

    def test_submit_review_rejects_swapped_typed_asset_ids(self) -> None:
        self._seed_review_auth()
        session = self._create_review_session()
        self._write_review_asset_meta("events-1", "events", session=session)
        self._write_review_asset_meta("audio-1", "audio", mime_type="audio/webm", session=session)

        response = self.client.post(
            "/v1/reviews",
            headers=self._auth_headers(session["token"]),
            json={
                "review_id": "review-swapped-assets",
                "subject_id": "https://staging.example.com/page",
                "submitted_by": "tester",
                "started_at": "2026-05-01T00:00:00Z",
                "stopped_at": "2026-05-01T00:00:10Z",
                "duration_ms": 10000,
                "project_id": "project-one",
                "deployment_id": "deployment-one",
                "events_asset_id": "audio-1",
                "audio_asset_id": "events-1",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("events_asset_id must reference a events asset", response.text)
        self.assertFalse((Path(os.environ["REVIEWS_DATA_DIR"]) / "review-swapped-assets.json").exists())

    def test_valid_session_uploads_assets_submits_review_and_preserves_queue_path(self) -> None:
        reviews_dir = Path(os.environ["REVIEWS_DATA_DIR"])
        self._seed_review_auth(label="Owner Label")
        session = self._create_review_session()

        events_upload = self.client.post(
            "/v1/reviews/assets",
            headers=self._auth_headers(session["token"]),
            files={"file": ("events.json", b"{}", "application/json")},
            data={"asset_type": "events", "project_id": "project-one", "deployment_id": "deployment-one"},
        )
        self.assertEqual(events_upload.status_code, 200, events_upload.text)
        audio_upload = self.client.post(
            "/v1/reviews/assets",
            headers=self._auth_headers(session["token"]),
            files={"file": ("audio.webm", b"audio", "audio/webm")},
            data={"asset_type": "audio", "project_id": "project-one", "deployment_id": "deployment-one"},
        )
        self.assertEqual(audio_upload.status_code, 200, audio_upload.text)
        events_id = events_upload.json()["asset_id"]
        audio_id = audio_upload.json()["asset_id"]

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
                headers=self._auth_headers(session["token"]),
                json={
                    "review_id": "review-typed-assets",
                    "subject_id": "https://staging.example.com/page",
                    "submitted_by": "tester",
                    "started_at": "2026-05-01T00:00:00Z",
                    "stopped_at": "2026-05-01T00:00:10Z",
                    "duration_ms": 10000,
                    "project_id": "project-one",
                    "deployment_id": "deployment-one",
                    "asset_ids": [],
                    "events_asset_id": events_id,
                    "audio_asset_id": audio_id,
                },
            )

        self.assertEqual(response.status_code, 200)
        saved = json.loads((reviews_dir / "review-typed-assets.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["events_asset_id"], events_id)
        self.assertEqual(saved["audio_asset_id"], audio_id)
        self.assertEqual(saved["client_id"], "client-1")
        self.assertEqual(saved["project_id"], "project-1")
        self.assertEqual(saved["deployment_id"], "deployment-1")
        self.assertEqual(saved["auth_session_id"], session["session_id"])
        self.assertTrue(saved["authenticated"])
        self.assertEqual(saved["submitted_by"], "Owner Label")
        self.assertEqual(saved["origin"], "https://staging.example.com")
        asset_meta = json.loads((reviews_dir / "assets" / f"{events_id}.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(asset_meta["auth_session_id"], session["session_id"])
        enqueue_payload = next(payload for url, payload in posted if url.endswith("/queues/workspace/enqueue"))
        self.assertNotIn("process_path", enqueue_payload)
        self.assertEqual(enqueue_payload["payload"]["events_asset_id"], events_id)
        self.assertEqual(enqueue_payload["sender"], "Owner Label")

    def test_submit_review_queue_failure_returns_cors_visible_error(self) -> None:
        self._seed_review_auth(origin="https://swrl-ui.vercel.app", subject_pattern="https://swrl-ui.vercel.app/*")
        session = self._create_review_session(
            origin="https://swrl-ui.vercel.app",
            subject_id="https://swrl-ui.vercel.app/",
        )
        self._write_review_asset_meta("events-cors", "events", session=session)

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url: str, json: dict):
                raise self_module.httpx.ConnectError("queue unavailable")

        self_module = self.module
        with patch.object(self.module.httpx, "AsyncClient", FakeAsyncClient):
            response = self.client.post(
                "/v1/reviews",
                headers=self._auth_headers(session["token"], origin="https://swrl-ui.vercel.app"),
                json={
                    "review_id": "review-queue-cors-visible",
                    "subject_id": "https://swrl-ui.vercel.app/",
                    "submitted_by": "tester",
                    "started_at": "2026-05-01T00:00:00Z",
                    "stopped_at": "2026-05-01T00:00:10Z",
                    "duration_ms": 10000,
                    "project_id": "project-one",
                    "deployment_id": "deployment-one",
                    "asset_ids": [],
                    "events_asset_id": "events-cors",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "https://swrl-ui.vercel.app")
        self.assertIn("review saved but queue enqueue failed", response.text)

    def test_deploy_hook_registers_review_deployment_and_client_can_authenticate(self) -> None:
        self._seed_review_auth(deployment_id="local-deployment", deployment_slug="local-deployment", project_scoped_access_code=True)
        deploy_token = self._seed_deploy_hook()

        response = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "swrl-ui-preview-main-abc123",
                "branch": "main",
                "allowed_origin": "https://swrl-ui-git-main-org.vercel.app",
                "subject_pattern": "https://swrl-ui-git-main-org.vercel.app/*",
                "vercel_deployment_id": "dpl_123",
                "commit_sha": "abc123",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["deployment"]["id"], "swrl-ui-preview-main-abc123")
        self.assertEqual(payload["deployment"]["allowed_origin"], "https://swrl-ui-git-main-org.vercel.app")
        self.assertFalse(payload["secrets_printed"])
        with sqlite3.connect(os.environ["CLIENTS_DB_PATH"]) as conn:
            hook_row = conn.execute("SELECT token_hash, last_used_at FROM review_deploy_hooks WHERE id = ?", ("hook-1",)).fetchone()
        self.assertIsNotNone(hook_row[1])
        self.assertNotIn("deploy-hook-secret", hook_row[0])

        session = self._create_review_session(
            deployment_id="swrl-ui-preview-main-abc123",
            origin="https://swrl-ui-git-main-org.vercel.app",
            subject_id="https://swrl-ui-git-main-org.vercel.app/review",
        )
        self.assertEqual(session["deployment_id"], "swrl-ui-preview-main-abc123")

    def test_deploy_hook_registration_accepts_secret_with_underscores(self) -> None:
        self._seed_review_auth(
            deployment_id="local-deployment",
            deployment_slug="local-deployment",
            project_scoped_access_code=True,
        )
        deploy_token = self._seed_deploy_hook(secret="deploy_hook_secret_with_underscores")

        response = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "swrl-ui-preview-underscore-secret",
                "branch": "main",
                "allowed_origin": "https://swrl-ui-git-main-org.vercel.app",
                "subject_pattern": "https://swrl-ui-git-main-org.vercel.app/*",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)

    def test_deploy_hook_registration_accepts_localhost_origin_with_port(self) -> None:
        self._seed_review_auth(deployment_id="local-deployment", deployment_slug="local-deployment", project_scoped_access_code=True)
        deploy_token = self._seed_deploy_hook()

        response = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "swrl-ui-local",
                "branch": "local",
                "allowed_origin": "http://localhost:5173",
                "subject_pattern": "http://localhost:5173/*",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["deployment"]["allowed_origin"], "http://localhost:5173")
        self.assertFalse(payload["secrets_printed"])

    def test_deploy_hook_rejects_non_local_http_origin(self) -> None:
        self._seed_review_auth(deployment_id="local-deployment", deployment_slug="local-deployment", project_scoped_access_code=True)
        deploy_token = self._seed_deploy_hook()

        response = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "bad-http-origin",
                "branch": "main",
                "allowed_origin": "http://swrl-ui-git-main-org.vercel.app",
                "subject_pattern": "http://swrl-ui-git-main-org.vercel.app/*",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("https outside local development", response.text)

    def test_deploy_hook_registration_is_idempotent_and_updates_origin(self) -> None:
        self._seed_review_auth(deployment_id="local-deployment", deployment_slug="local-deployment", project_scoped_access_code=True)
        deploy_token = self._seed_deploy_hook()
        first = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "swrl-ui-preview-feature",
                "branch": "feature/one",
                "allowed_origin": "https://swrl-ui-git-feature-one-org.vercel.app",
                "subject_pattern": "https://swrl-ui-git-feature-one-org.vercel.app/*",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)

        second = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "swrl-ui-preview-feature",
                "branch": "feature/two",
                "allowed_origin": "https://swrl-ui-git-feature-two-org.vercel.app",
                "subject_pattern": "https://swrl-ui-git-feature-two-org.vercel.app/*",
            },
        )
        self.assertEqual(second.status_code, 200, second.text)

        with sqlite3.connect(os.environ["CLIENTS_DB_PATH"]) as conn:
            row = conn.execute(
                "SELECT branch, allowed_origin, subject_pattern FROM review_deployments WHERE slug = ?",
                ("swrl-ui-preview-feature",),
            ).fetchone()
        self.assertEqual(row[0], "feature/two")
        self.assertEqual(row[1], "https://swrl-ui-git-feature-two-org.vercel.app")
        self.assertEqual(row[2], "https://swrl-ui-git-feature-two-org.vercel.app/*")

    def test_deploy_hook_rejects_bad_token_and_malformed_origin(self) -> None:
        self._seed_review_auth(deployment_id="local-deployment", deployment_slug="local-deployment", project_scoped_access_code=True)
        deploy_token = self._seed_deploy_hook()
        bad_token = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": "Bearer wrong"},
            json={
                "project_id": "project-one",
                "deployment_slug": "bad-token",
                "branch": "main",
                "allowed_origin": "https://swrl-ui-git-main-org.vercel.app",
                "subject_pattern": "https://swrl-ui-git-main-org.vercel.app/*",
            },
        )
        self.assertEqual(bad_token.status_code, 401)

        malformed_origin = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "bad-origin",
                "branch": "main",
                "allowed_origin": "https://evil.example.com",
                "subject_pattern": "https://evil.example.com/*",
            },
        )
        self.assertEqual(malformed_origin.status_code, 422)

        concatenated_suffix = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "bad-concatenated-suffix",
                "branch": "main",
                "allowed_origin": "https://evilvercel.app",
                "subject_pattern": "https://evilvercel.app/*",
            },
        )
        self.assertEqual(concatenated_suffix.status_code, 422)

        mismatched_subject = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "bad-subject",
                "branch": "main",
                "allowed_origin": "https://swrl-ui-git-main-org.vercel.app",
                "subject_pattern": "https://other.vercel.app/*",
            },
        )
        self.assertEqual(mismatched_subject.status_code, 422)

    def test_deploy_hook_rejects_inactive_hook_and_wrong_project(self) -> None:
        self._seed_review_auth(deployment_id="local-deployment", deployment_slug="local-deployment", project_scoped_access_code=True)
        inactive_token = self._seed_deploy_hook(hook_id="inactive-hook", active=0)
        inactive = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {inactive_token}"},
            json={
                "project_id": "project-one",
                "deployment_slug": "inactive-preview",
                "branch": "main",
                "allowed_origin": "https://swrl-ui-git-main-org.vercel.app",
                "subject_pattern": "https://swrl-ui-git-main-org.vercel.app/*",
            },
        )
        self.assertEqual(inactive.status_code, 401)

        deploy_token = self._seed_deploy_hook(hook_id="project-hook")
        wrong_project = self.client.post(
            "/v1/review-auth/deployments/register",
            headers={"Authorization": f"Bearer {deploy_token}"},
            json={
                "project_id": "other-project",
                "deployment_slug": "wrong-project-preview",
                "branch": "main",
                "allowed_origin": "https://swrl-ui-git-main-org.vercel.app",
                "subject_pattern": "https://swrl-ui-git-main-org.vercel.app/*",
            },
        )
        self.assertEqual(wrong_project.status_code, 401)

    def test_valid_access_code_creates_short_lived_session_and_get_validates_it(self) -> None:
        self._seed_review_auth()
        session = self._create_review_session()
        self.assertEqual(session["project_id"], "project-1")
        self.assertEqual(session["deployment_id"], "deployment-1")
        self.assertEqual(session["label"], "Tester")
        self.assertTrue(session["token"].startswith("rev_"))

        response = self.client.get(
            "/v1/review-auth/session",
            headers=self._auth_headers(session["token"]),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["session_id"], session["session_id"])
        self.assertEqual(payload["expires_at"], session["expires_at"])

    def test_review_auth_rejects_wrong_origin_project_deployment_and_subject(self) -> None:
        self._seed_review_auth()
        bad_origin = self.client.post(
            "/v1/review-auth/session",
            headers={"Origin": "https://evil.example.com"},
            json={
                "project_id": "project-one",
                "deployment_id": "deployment-one",
                "email": "owner@example.com",
                "access_code": "let-me-review",
                "subject_id": "https://evil.example.com/page",
            },
        )
        self.assertEqual(bad_origin.status_code, 401)

        bad_subject = self.client.post(
            "/v1/review-auth/session",
            headers={"Origin": "https://staging.example.com"},
            json={
                "project_id": "project-one",
                "deployment_id": "deployment-one",
                "email": "owner@example.com",
                "access_code": "let-me-review",
                "subject_id": "https://other.example.com/page",
            },
        )
        self.assertEqual(bad_subject.status_code, 401)

        session = self._create_review_session()
        wrong_origin = self.client.get(
            "/v1/review-auth/session",
            headers=self._auth_headers(session["token"], origin="https://evil.example.com"),
        )
        self.assertEqual(wrong_origin.status_code, 401)
        wrong_project = self.client.post(
            "/v1/reviews/assets",
            headers=self._auth_headers(session["token"]),
            files={"file": ("events.json", b"{}", "application/json")},
            data={"asset_type": "events", "project_id": "other-project", "deployment_id": "deployment-one"},
        )
        self.assertEqual(wrong_project.status_code, 401)
        wrong_deployment = self.client.post(
            "/v1/reviews/assets",
            headers=self._auth_headers(session["token"]),
            files={"file": ("events.json", b"{}", "application/json")},
            data={"asset_type": "events", "project_id": "project-one", "deployment_id": "other-deployment"},
        )
        self.assertEqual(wrong_deployment.status_code, 401)

    def test_review_rejects_assets_from_a_different_session(self) -> None:
        self._seed_review_auth()
        session_one = self._create_review_session(subject_id="https://staging.example.com/one")
        session_two = self._create_review_session(subject_id="https://staging.example.com/two")
        self._write_review_asset_meta("events-1", "events", session=session_one)
        self._write_review_asset_meta("audio-1", "audio", mime_type="audio/webm", session=session_one)

        response = self.client.post(
            "/v1/reviews",
            headers=self._auth_headers(session_two["token"]),
            json={
                "review_id": "review-cross-session",
                "subject_id": "https://staging.example.com/two",
                "submitted_by": "tester",
                "started_at": "2026-05-01T00:00:00Z",
                "stopped_at": "2026-05-01T00:00:10Z",
                "duration_ms": 10000,
                "project_id": "project-one",
                "deployment_id": "deployment-one",
                "events_asset_id": "events-1",
                "audio_asset_id": "audio-1",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("different review session", response.text)

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

    def test_clients_db_is_canonical_and_clients_have_rolodex_path(self) -> None:
        self._seed_review_auth()
        self.assertTrue(Path(os.environ["CLIENTS_DB_PATH"]).exists())
        with sqlite3.connect(os.environ["CLIENTS_DB_PATH"]) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
            self.assertIn("rolodex_entry_path", columns)
            row = conn.execute("SELECT rolodex_entry_path FROM clients WHERE id = ?", ("client-1",)).fetchone()
            self.assertEqual(row[0], "notes/Client One.md")

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
            json={
                "status": "processed",
                "review_note_path": "local-private://vault/notes/review review-123.md",
                "review_packet_path": "local-private://cases/case-123/artifacts/review_packet.json",
                "review_packet_status": "review_packet_ready",
                "reason": "review_packet_ready",
                "automaton_status": "review",
                "automaton_event": "processing_done",
                "review_outcome": "review_packet_ready",
                "review_scope": "full_output_against_objective_process_prompt_acceptance_criteria",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "processed")

        saved = json.loads(review_path.read_text())
        self.assertEqual(saved["status"], "processed")
        self.assertEqual(saved["review_note_path"], "local-private://vault/notes/review review-123.md")
        self.assertEqual(saved["review_packet_path"], "local-private://cases/case-123/artifacts/review_packet.json")
        self.assertEqual(saved["review_packet_status"], "review_packet_ready")
        self.assertEqual(saved["status_reason"], "review_packet_ready")
        self.assertEqual(saved["automaton_status"], "review")
        self.assertEqual(saved["automaton_event"], "processing_done")
        self.assertEqual(saved["review_outcome"], "review_packet_ready")
        self.assertEqual(saved["review_scope"], "full_output_against_objective_process_prompt_acceptance_criteria")
        self.assertEqual(saved["subject_id"], "http://example")

    def test_patch_review_status_accepts_old_shape_and_allowed_statuses(self) -> None:
        reviews_dir = Path(os.environ["REVIEWS_DATA_DIR"])
        allowed_statuses = {"queued", "processing", "processed", "failed"}
        for status in sorted(allowed_statuses):
            review_path = reviews_dir / f"review-{status}.json"
            review_path.write_text(json.dumps({"review_id": f"review-{status}", "status": "queued"}))

            response = self.client.patch(
                f"/v1/reviews/review-{status}/status",
                json={"status": status, "review_note_path": f"local-private://vault/notes/{status}.md"},
            )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], status)
            saved = json.loads(review_path.read_text())
            self.assertEqual(saved["status"], status)
            self.assertEqual(saved["review_note_path"], f"local-private://vault/notes/{status}.md")
            self.assertNotIn("automaton_status", saved)

        review_path = reviews_dir / "review-succeeded.json"
        review_path.write_text(json.dumps({"review_id": "review-succeeded", "status": "queued"}))
        response = self.client.patch("/v1/reviews/review-succeeded/status", json={"status": "succeeded"})
        self.assertEqual(response.status_code, 422)
