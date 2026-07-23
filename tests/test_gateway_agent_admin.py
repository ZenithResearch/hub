from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from libs.common.proto import agent_admin_pb2
from services.gateway_http.app import create_app


class FakeAgentAdminStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def GetProfile(self, request, *, timeout):
        self.calls.append(("GetProfile", request))
        return agent_admin_pb2.Profile(
            profile_id=request.profile_id,
            revision=3,
            desired_state=agent_admin_pb2.DESIRED_STATE_ENABLED,
            observed_state=agent_admin_pb2.OBSERVED_STATE_RUNNING,
            ssm_managed=True,
            secrets_printed=False,
        )

    async def RegisterProfile(self, request, *, timeout):
        self.calls.append(("RegisterProfile", request))
        return agent_admin_pb2.Profile(
            profile_id=request.profile_id,
            revision=1,
            secrets_printed=False,
        )

    async def RequestLifecycleOperation(self, request, *, timeout):
        self.calls.append(("RequestLifecycleOperation", request))
        return agent_admin_pb2.LifecycleOperation(
            operation_id="12345678-abcd-1234-abcd-123456789012",
            profile_id=request.profile_id,
            action=request.action,
            state=agent_admin_pb2.OPERATION_STATE_DISPATCHED,
            provider_operation_ref="12345678-abcd-1234-abcd-123456789012",
            secrets_printed=False,
        )


def _client(tmp_path: Path) -> tuple[TestClient, FakeAgentAdminStub]:
    os.environ["REVIEW_ACCESS_ADMIN_TOKEN"] = "unrelated-review-admin-token"
    os.environ["AGENT_ADMIN_BEARER_TOKEN"] = "agent-admin-test-token-32-characters"
    os.environ["CLIENTS_DB_PATH"] = str(tmp_path / "clients.db")
    os.environ["HUB_CONFIG_SECRETS_PATH"] = str(tmp_path / "config.env")
    client = TestClient(create_app())
    client.__enter__()
    stub = FakeAgentAdminStub()
    client.app.state.agent_admin_stub = stub
    return client, stub


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer agent-admin-test-token-32-characters"}


def test_agent_admin_projection_requires_existing_admin_auth(tmp_path: Path) -> None:
    client, stub = _client(tmp_path)
    try:
        response = client.get("/v1/admin/agents/cloudproof")
        assert response.status_code == 401
        assert stub.calls == []

        review_admin = client.get(
            "/v1/admin/agents/cloudproof",
            headers={"Authorization": "Bearer unrelated-review-admin-token"},
        )
        assert review_admin.status_code == 401
        assert stub.calls == []

        response = client.get("/v1/admin/agents/cloudproof", headers=_headers())
        assert response.status_code == 200
        assert response.json()["profile_id"] == "cloudproof"
        assert response.json()["secrets_printed"] is False
        assert "agent-admin-test-token-32-characters" not in response.text
    finally:
        client.__exit__(None, None, None)


def test_agent_admin_registration_requires_idempotency_key(tmp_path: Path) -> None:
    client, stub = _client(tmp_path)
    try:
        missing = client.post(
            "/v1/admin/agents/cloudproof/register",
            headers=_headers(),
        )
        assert missing.status_code == 422
        assert stub.calls == []

        response = client.post(
            "/v1/admin/agents/cloudproof/register",
            headers={**_headers(), "Idempotency-Key": "register-cloudproof"},
        )
        assert response.status_code == 200
        request = stub.calls[-1][1]
        assert request.idempotency_key == "register-cloudproof"
    finally:
        client.__exit__(None, None, None)


def test_agent_admin_lifecycle_projection_has_only_typed_operation_fields(
    tmp_path: Path,
) -> None:
    client, stub = _client(tmp_path)
    try:
        response = client.post(
            "/v1/admin/agents/cloudproof/lifecycle-operations",
            headers=_headers(),
            json={
                "action": "restart",
                "expected_revision": 3,
                "idempotency_key": "restart-cloudproof-3",
            },
        )
        assert response.status_code == 200
        request = stub.calls[-1][1]
        assert request.action == agent_admin_pb2.LIFECYCLE_ACTION_RESTART
        assert request.expected_revision == 3
        assert request.idempotency_key == "restart-cloudproof-3"
        for forbidden in ("command", "arguments", "prompt", "tool", "secret_value"):
            assert forbidden not in request.DESCRIPTOR.fields_by_name
            assert forbidden not in response.text.lower()
        assert response.json()["secrets_printed"] is False
    finally:
        client.__exit__(None, None, None)
