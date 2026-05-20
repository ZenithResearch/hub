from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class ModelProfileResolverTests(unittest.TestCase):
    def test_resolves_effective_profile_without_raw_secret(self) -> None:
        from libs.common.model_profiles import load_model_profile_contract, resolve_effective_model_profile

        contract = load_model_profile_contract(Path("infra/model-profiles.yaml"))
        effective = resolve_effective_model_profile(
            contract,
            agent="frank",
            profile="review_brief_compiler",
            deployment_profile="cloud-aws-prod",
        )

        assert effective["agent"] == "frank"
        assert effective["profile"] == "review_brief_compiler"
        assert effective["deployment_profile"] == "cloud-aws-prod"
        assert effective["provider"] == "hub-internal-openai-compatible"
        assert effective["endpoint_ref"] == "prod-llama-server"
        assert effective["endpoint"]["base_url"] == "http://llama-server.zenith-hub-prod.local:3690/v1"
        assert effective["model"] == "Qwen3.5-9B-Q4_K_M.gguf"
        assert effective["secret"] == {
            "ref": "none",
            "configured": False,
            "display": "No bearer secret; internal/private endpoint only.",
        }
        assert effective["fallback_profile"] == "fallback_fast"
        assert effective["bootstrap_env"] == {
            "FRANK_MODEL": "Qwen3.5-9B-Q4_K_M.gguf",
            "OPENAI_BASE_URL": "http://llama-server.zenith-hub-prod.local:3690/v1",
        }
        serialized = json.dumps(effective)
        assert "OPENAI_API_KEY" not in serialized
        assert "sk-" not in serialized
        assert "Bearer " not in serialized

    def test_unknown_profile_blocks_visibly(self) -> None:
        from libs.common.model_profiles import ModelProfileResolutionError, load_model_profile_contract, resolve_effective_model_profile

        contract = load_model_profile_contract(Path("infra/model-profiles.yaml"))
        with self.assertRaises(ModelProfileResolutionError) as ctx:
            resolve_effective_model_profile(
                contract,
                agent="frank",
                profile="does_not_exist",
                deployment_profile="cloud-aws-prod",
            )
        assert "unknown profile" in str(ctx.exception)


class _FakeOpenAIResponse:
    status_code = 200

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "ok"}}]}


class ModelProfileConnectivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_connectivity_check_posts_minimal_chat_probe_without_secret(self) -> None:
        from libs.common.model_profiles import (
            check_model_profile_connectivity,
            load_model_profile_contract,
            resolve_effective_model_profile,
        )

        contract = load_model_profile_contract(Path("infra/model-profiles.yaml"))
        effective = resolve_effective_model_profile(
            contract,
            agent="frank",
            profile="review_brief_compiler",
            deployment_profile="cloud-aws-prod",
        )
        calls: list[dict] = []

        async def fake_post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> _FakeOpenAIResponse:
            calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
            return _FakeOpenAIResponse()

        result = await check_model_profile_connectivity(effective, post_json=fake_post_json)

        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["endpoint_ref"] == "prod-llama-server"
        assert result["model"] == "Qwen3.5-9B-Q4_K_M.gguf"
        assert result["secrets_printed"] is False
        assert calls == [
            {
                "url": "http://llama-server.zenith-hub-prod.local:3690/v1/chat/completions",
                "payload": {
                    "model": "Qwen3.5-9B-Q4_K_M.gguf",
                    "messages": [{"role": "user", "content": "health check"}],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                },
                "headers": {},
                "timeout": 120,
            }
        ]
        serialized = json.dumps(result)
        assert "OPENAI_API_KEY" not in serialized
        assert "sk-" not in serialized
        assert "Bearer " not in serialized


class GatewayModelProfileAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        reviews_dir = root / "reviews"
        reviews_dir.mkdir(parents=True)
        hermes_dir = root / ".hermes"
        hermes_dir.mkdir(parents=True)

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
        os.environ["CORS_ALLOW_ORIGINS"] = "https://staging.example.com"
        os.environ["RUNTIME_GRPC_TARGET"] = "runtime-grpc:50051"
        os.environ["HERMES_SESSION_ROOTS"] = str(hermes_dir)
        os.environ["HUB_CONFIG_SECRETS_PATH"] = str(root / "config-secrets.env")
        os.environ["MODEL_PROFILES_PATH"] = "infra/model-profiles.yaml"
        os.environ["MODEL_PROFILE_OVERRIDES_PATH"] = str(root / "model-profile-overrides.yaml")
        os.environ["MODEL_PROFILE_AUDIT_PATH"] = str(root / "model-profile-audit.jsonl")

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
        sys.modules["libs.common.proto.agent_pb2_grpc"] = types.SimpleNamespace(AgentRuntimeStub=lambda channel: object())
        sys.modules["libs.common.proto.agent_pb2"] = types.SimpleNamespace()

        sys.modules.pop("services.gateway_http.app", None)
        module = importlib.import_module("services.gateway_http.app")
        self.module = importlib.reload(module)
        self.client_context = TestClient(self.module.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
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
        os.environ.pop("MODEL_PROFILES_PATH", None)
        os.environ.pop("MODEL_PROFILE_OVERRIDES_PATH", None)
        os.environ.pop("MODEL_PROFILE_AUDIT_PATH", None)
        self.tmpdir.cleanup()

    def test_admin_effective_profile_endpoint_requires_admin_token(self) -> None:
        response = self.client.get(
            "/v1/admin/model-profiles/effective",
            params={"agent": "frank", "profile": "review_brief_compiler", "deployment_profile": "cloud-aws-prod"},
        )

        assert response.status_code == 401

    def test_admin_effective_profile_endpoint_returns_safe_config(self) -> None:
        response = self.client.get(
            "/v1/admin/model-profiles/effective",
            params={"agent": "frank", "profile": "review_brief_compiler", "deployment_profile": "cloud-aws-prod"},
            headers={"Authorization": "Bearer admin-secret"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["agent"] == "frank"
        assert payload["profile"] == "review_brief_compiler"
        assert payload["model"] == "Qwen3.5-9B-Q4_K_M.gguf"
        assert payload["endpoint"]["base_url"] == "http://llama-server.zenith-hub-prod.local:3690/v1"
        assert payload["secret"]["ref"] == "none"
        assert payload["secret"]["configured"] is False
        assert payload["secrets_printed"] is False
        serialized = json.dumps(payload)
        assert "OPENAI_API_KEY" not in serialized
        assert "sk-" not in serialized
        assert "Bearer " not in serialized

    def test_admin_connectivity_check_endpoint_returns_redacted_status(self) -> None:
        async def fake_check(effective: dict) -> dict:
            assert effective["agent"] == "frank"
            return {
                "ok": True,
                "agent": "frank",
                "profile": "review_brief_compiler",
                "deployment_profile": "cloud-aws-prod",
                "endpoint_ref": "prod-llama-server",
                "model": "Qwen3.5-9B-Q4_K_M.gguf",
                "status_code": 200,
                "latency_ms": 12,
                "secrets_printed": False,
            }

        self.module.check_model_profile_connectivity = fake_check
        response = self.client.post(
            "/v1/admin/model-profiles/connectivity-check",
            params={"agent": "frank", "profile": "review_brief_compiler", "deployment_profile": "cloud-aws-prod"},
            headers={"Authorization": "Bearer admin-secret"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["endpoint_ref"] == "prod-llama-server"
        assert payload["model"] == "Qwen3.5-9B-Q4_K_M.gguf"
        assert payload["secrets_printed"] is False
        serialized = json.dumps(payload)
        assert "OPENAI_API_KEY" not in serialized
        assert "sk-" not in serialized
        assert "Bearer " not in serialized


    def test_admin_binding_update_writes_override_and_audit_without_secret(self) -> None:
        response = self.client.put(
            "/v1/admin/model-profiles/bindings",
            params={"agent": "frank", "profile": "review_brief_compiler", "deployment_profile": "cloud-aws-prod"},
            headers={"Authorization": "Bearer admin-secret", "X-Zenith-Operator": "zenithos-test"},
            json={
                "updates": {
                    "model": "Qwen3.5-9B-Q4_K_M.gguf",
                    "temperature": 0.15,
                    "max_tokens": 1536,
                    "secret_ref": "none",
                },
                "connectivity_result": {"ok": True, "status_code": 200, "secrets_printed": False},
            },
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["agent"] == "frank"
        assert payload["profile"] == "review_brief_compiler"
        assert payload["deployment_profile"] == "cloud-aws-prod"
        assert payload["audit"]["actor"] == "zenithos-test"
        assert payload["audit"]["old_effective_config_hash"] != payload["audit"]["new_effective_config_hash"]
        assert payload["effective"]["temperature"] == 0.15
        assert payload["effective"]["max_tokens"] == 1536
        assert payload["secrets_printed"] is False

        audit_path = Path(os.environ["MODEL_PROFILE_AUDIT_PATH"])
        audit_record = json.loads(audit_path.read_text().splitlines()[-1])
        assert audit_record["actor"] == "zenithos-test"
        assert audit_record["connectivity_check_result"] == {"ok": True, "status_code": 200, "secrets_printed": False}
        serialized = json.dumps(payload) + audit_path.read_text()
        assert "OPENAI_API_KEY" not in serialized
        assert "sk-" not in serialized
        assert "Bearer " not in serialized

        effective_response = self.client.get(
            "/v1/admin/model-profiles/effective",
            params={"agent": "frank", "profile": "review_brief_compiler", "deployment_profile": "cloud-aws-prod"},
            headers={"Authorization": "Bearer admin-secret"},
        )
        assert effective_response.status_code == 200
        assert effective_response.json()["temperature"] == 0.15
        assert effective_response.json()["max_tokens"] == 1536
