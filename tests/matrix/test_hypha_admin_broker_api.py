from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from services.hypha_admin_broker.api import create_app
from services.hypha_admin_broker.auth import BrokerSessionStore, encode_scrypt_verifier
from services.hypha_admin_broker.secret_store import (
    AtomicFileSecretVerifierStore,
    SecretVerifierStoreError,
)
from services.hypha_admin_broker.synapse import SynapseAuthorityRejected

SECRET = "correct-administration-secret-value-1234"
TOKEN = bytes(range(32))
EXPECTED_SESSION_TOKEN = base64.urlsafe_b64encode(TOKEN).rstrip(b"=").decode("ascii")  # private-artifact-scan: allow-test-fixture


class Clock:
    def __init__(self, now: float = 1_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeSynapseAdmin:
    def __init__(self):
        self.ready_calls = 0
        self.snapshot_calls = 0

    async def ready(self) -> None:
        self.ready_calls += 1

    async def snapshot(self) -> dict[str, object]:
        self.snapshot_calls += 1
        return {
            "users": [
                {
                    "user_id": "@alice:example.org",
                    "is_administrator": False,
                    "is_deactivated": False,
                    "is_guest": False,
                    "user_type": None,
                }
            ],
            "rooms": [
                {
                    "room_id": "!room:example.org",
                    "name": "Room",
                    "joined_member_count": 1,
                }
            ],
        }


def make_client(
    *,
    secret_verifier_store: AtomicFileSecretVerifierStore | None = None,
) -> tuple[TestClient, FakeSynapseAdmin]:
    verifier = encode_scrypt_verifier(SECRET, salt=b"0123456789abcdef", n=2**10, r=8, p=1)
    store = BrokerSessionStore(
        verifier=verifier,
        clock=Clock(),
        token_factory=lambda size: TOKEN,
        idle_timeout_seconds=120,
        absolute_timeout_seconds=600,
    )
    synapse = FakeSynapseAdmin()
    app = create_app(
        session_store=store,
        synapse=synapse,
        secret_verifier_store=secret_verifier_store,
    )
    return TestClient(app), synapse


def authenticate(client: TestClient) -> str:
    response = client.post("/_hypha/admin/v1/session", json={"secret": SECRET})
    assert response.status_code == 201
    return response.json()["session_token"]


def assert_security_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_session_exchange_returns_only_bounded_opaque_session_metadata():
    client, _ = make_client()

    response = client.post("/_hypha/admin/v1/session", json={"secret": SECRET})

    assert response.status_code == 201
    assert response.json() == {
        "session_token": EXPECTED_SESSION_TOKEN,
        "expires_in_seconds": 600,
        "idle_timeout_seconds": 120,
    }
    assert SECRET not in response.text
    assert "verifier" not in response.text.lower()
    assert_security_headers(response)


def test_invalid_secret_and_invalid_body_have_generic_non_reflective_failures():
    client, _ = make_client()
    attempted = "wrong-administration-secret-value-5678"

    wrong = client.post("/_hypha/admin/v1/session", json={"secret": attempted})
    malformed = client.post(
        "/_hypha/admin/v1/session",
        json={"secret": attempted, "unexpected": attempted},
    )

    assert wrong.status_code == 401
    assert wrong.json() == {"error": "administration authentication failed"}
    assert malformed.status_code == 400
    assert malformed.json() == {"error": "invalid request"}
    for response in [wrong, malformed]:
        assert attempted not in response.text
        assert_security_headers(response)


def test_snapshot_requires_exact_bearer_scheme_and_calls_only_typed_adapter():
    client, synapse = make_client()
    token = authenticate(client)

    for authorization in [None, token, f"Basic {token}", f"Bearer  {token}", "Bearer"]:
        headers = {} if authorization is None else {"Authorization": authorization}
        denied = client.get("/_hypha/admin/v1/snapshot", headers=headers)
        assert denied.status_code == 401
        assert denied.json() == {"error": "administration session is invalid or expired"}
        assert_security_headers(denied)
    assert synapse.snapshot_calls == 0

    response = client.get(
        "/_hypha/admin/v1/snapshot",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["users"][0]["user_id"] == "@alice:example.org"
    assert response.json()["rooms"][0]["room_id"] == "!room:example.org"
    assert synapse.snapshot_calls == 1
    assert token not in response.text
    assert_security_headers(response)


def test_logout_revokes_session_and_is_not_replayable():
    client, synapse = make_client()
    token = authenticate(client)
    headers = {"Authorization": f"Bearer {token}"}

    logout = client.delete("/_hypha/admin/v1/session", headers=headers)
    denied = client.get("/_hypha/admin/v1/snapshot", headers=headers)
    replay = client.delete("/_hypha/admin/v1/session", headers=headers)

    assert logout.status_code == 204
    assert logout.content == b""
    assert_security_headers(logout)
    assert denied.status_code == 401
    assert replay.status_code == 401
    assert synapse.snapshot_calls == 0


def test_liveness_and_authority_readiness_are_unauthenticated_and_expose_no_configuration():
    client, synapse = make_client()

    health = client.get("/_hypha/admin/v1/health")
    ready = client.get("/_hypha/admin/v1/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert synapse.ready_calls == 1
    assert_security_headers(health)
    assert_security_headers(ready)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_readiness_fails_generically_when_service_authority_is_unavailable():
    client, synapse = make_client()

    async def unavailable() -> None:
        raise OSError("sensitive upstream detail")

    synapse.ready = unavailable  # type: ignore[method-assign]
    response = client.get("/_hypha/admin/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"error": "homeserver administration is unavailable"}
    assert "sensitive" not in response.text
    assert_security_headers(response)


def test_browser_preflight_content_type_and_request_size_fail_closed():
    client, _ = make_client()

    preflight = client.options(
        "/_hypha/admin/v1/session",
        headers={
            "Origin": "https://attacker.invalid",
            "Access-Control-Request-Method": "POST",
        },
    )
    wrong_type = client.post(
        "/_hypha/admin/v1/session",
        content='{"secret":"' + SECRET + '"}',
        headers={"content-type": "text/plain"},
    )
    oversized = client.post(
        "/_hypha/admin/v1/session",
        json={"secret": "x" * (64 * 1024)},
    )

    assert preflight.status_code == 405
    assert "access-control-allow-origin" not in preflight.headers
    assert wrong_type.status_code == 415
    assert wrong_type.json() == {"error": "invalid request"}
    assert oversized.status_code == 413
    assert oversized.json() == {"error": "request is too large"}
    for response in [preflight, wrong_type, oversized]:
        assert_security_headers(response)


def test_malformed_secret_has_the_same_generic_authentication_rejection():
    client, _ = make_client()

    response = client.post("/_hypha/admin/v1/session", json={"secret": "short"})

    assert response.status_code == 401
    assert response.json() == {"error": "administration authentication failed"}


def test_snapshot_response_is_bounded_before_returning_upstream_data():
    client, synapse = make_client()
    token = authenticate(client)
    synapse.snapshot = lambda: None  # type: ignore[method-assign]

    async def huge_snapshot():
        return {
            "users": [
                {
                    "user_id": "@" + ("x" * 470) + f"{index}:example.org",
                    "is_administrator": False,
                    "is_deactivated": False,
                    "is_guest": False,
                    "user_type": None,
                }
                for index in range(3_000)
            ],
            "rooms": [],
        }

    synapse.snapshot = huge_snapshot  # type: ignore[method-assign]
    response = client.get(
        "/_hypha/admin/v1/snapshot",
        headers={"authorization": f"Bearer {token}"},
    )

    assert response.status_code == 502
    assert response.json() == {"error": "homeserver administration response was too large"}


def test_upstream_authority_rejection_revokes_the_broker_session():
    client, synapse = make_client()
    token = authenticate(client)

    async def rejected_snapshot():
        raise SynapseAuthorityRejected()

    synapse.snapshot = rejected_snapshot  # type: ignore[method-assign]
    headers = {"authorization": f"Bearer {token}"}
    rejected = client.get("/_hypha/admin/v1/snapshot", headers=headers)
    replay = client.get("/_hypha/admin/v1/snapshot", headers=headers)

    assert rejected.status_code == 401
    assert replay.status_code == 401
    assert rejected.json() == {"error": "administration session is invalid or expired"}


def test_capabilities_are_authenticated_and_advertise_only_configured_rotation(tmp_path):
    unavailable, _ = make_client()
    denied = unavailable.get("/_hypha/admin/v1/capabilities")
    assert denied.status_code == 401
    token = authenticate(unavailable)
    legacy = unavailable.get(
        "/_hypha/admin/v1/capabilities",
        headers={"authorization": f"Bearer {token}"},
    )
    assert legacy.status_code == 200
    assert legacy.json() == {"contract_version": 1, "features": []}

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    verifier_store = AtomicFileSecretVerifierStore(str(state / "operator-secret.verifier"))
    verifier_store.load_or_initialize(
        encode_scrypt_verifier(SECRET, salt=b"0123456789abcdef", n=2**10, r=8, p=1)
    )
    configured, _ = make_client(secret_verifier_store=verifier_store)
    configured_token = authenticate(configured)
    capabilities = configured.get(
        "/_hypha/admin/v1/capabilities",
        headers={"authorization": f"Bearer {configured_token}"},
    )
    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "contract_version": 1,
        "features": ["secret_rotation"],
    }


def test_rotation_persists_verifier_revokes_sessions_and_returns_no_secret(tmp_path):
    replacement = "replacement-administration-secret-value-5678"
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    verifier_path = state / "operator-secret.verifier"
    verifier_store = AtomicFileSecretVerifierStore(str(verifier_path))
    verifier_store.load_or_initialize(
        encode_scrypt_verifier(SECRET, salt=b"0123456789abcdef", n=2**10, r=8, p=1)
    )
    client, _ = make_client(secret_verifier_store=verifier_store)
    token = authenticate(client)
    headers = {"authorization": f"Bearer {token}"}

    invalid = client.post(
        "/_hypha/admin/v1/secret/rotate",
        headers=headers,
        json={"new_secret": replacement, "confirmation": "wrong"},
    )
    rotated = client.post(
        "/_hypha/admin/v1/secret/rotate",
        headers=headers,
        json={"new_secret": replacement, "confirmation": "rotate_admin_secret"},
    )

    assert invalid.status_code == 400
    assert rotated.status_code == 204
    assert rotated.content == b""
    assert replacement not in verifier_path.read_text(encoding="ascii")
    assert client.get("/_hypha/admin/v1/snapshot", headers=headers).status_code == 401
    assert client.post("/_hypha/admin/v1/session", json={"secret": SECRET}).status_code == 401
    assert client.post(
        "/_hypha/admin/v1/session",
        json={"secret": replacement},
    ).status_code == 201


def test_rotation_persistence_failure_keeps_current_authority_and_leaks_nothing():
    replacement = "replacement-administration-secret-value-5678"

    class FailingStore:
        def replace(self, _verifier: str) -> None:
            raise SecretVerifierStoreError()

    client, _ = make_client(secret_verifier_store=FailingStore())  # type: ignore[arg-type]
    token = authenticate(client)
    headers = {"authorization": f"Bearer {token}"}

    response = client.post(
        "/_hypha/admin/v1/secret/rotate",
        headers=headers,
        json={"new_secret": replacement, "confirmation": "rotate_admin_secret"},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "administration secret rotation is unavailable"}
    assert replacement not in response.text
    assert client.get("/_hypha/admin/v1/snapshot", headers=headers).status_code == 200
