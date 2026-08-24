from __future__ import annotations

from fastapi.testclient import TestClient

from services.hypha_admin_broker.api import create_app
from services.hypha_admin_broker.auth import BrokerSessionStore, encode_scrypt_verifier

SECRET = "correct-administration-secret-value-1234"
TOKEN = bytes(range(32))


class RecordingAdmin:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def snapshot(self):
        return {"users": [], "rooms": []}

    async def create_account(self, **payload):
        self.calls.append(("create_account", payload))
        return {
            "user_id": "@alice:example.org",
            "is_administrator": payload["administrator"],
            "is_deactivated": False,
            "is_guest": False,
            "user_type": None,
        }

    async def set_administrator(self, **payload):
        self.calls.append(("set_administrator", payload))
        return {
            "user_id": payload["user_id"],
            "is_administrator": payload["administrator"],
            "is_deactivated": False,
            "is_guest": False,
            "user_type": None,
        }

    async def password_reset_requests(self):
        self.calls.append(("password_reset_requests", {}))
        return [
            {
                "user_id": "@alice:example.org",
                "request_id": "11111111-1111-4111-8111-111111111111",
                "requested_at_ms": 1_786_000_000_000,
            }
        ]

    async def reset_password(self, **payload):
        self.calls.append(("reset_password", payload))

    async def create_room(self, **payload):
        self.calls.append(("create_room", payload))
        return {
            "room_id": "!room:example.org",
            "name": payload["name"],
            "joined_member_count": 1,
        }

    async def logout_account(self, **payload):
        self.calls.append(("logout_account", payload))

    async def deactivate_account(self, **payload):
        self.calls.append(("deactivate_account", payload))

    async def purge_room(self, **payload):
        self.calls.append(("purge_room", payload))


def make_client() -> tuple[TestClient, RecordingAdmin, str]:
    store = BrokerSessionStore(
        verifier=encode_scrypt_verifier(SECRET, salt=b"0123456789abcdef", n=2**10, r=8, p=1),
        token_factory=lambda size: TOKEN,
    )
    admin = RecordingAdmin()
    client = TestClient(create_app(session_store=store, synapse=admin))
    response = client.post("/_hypha/admin/v1/session", json={"secret": SECRET})
    assert response.status_code == 201
    return client, admin, response.json()["session_token"]


def test_account_and_password_reset_operations_are_typed_and_session_gated():
    client, admin, token = make_client()
    headers = {"Authorization": f"Bearer {token}"}

    unauthorized = client.post(
        "/_hypha/admin/v1/users",
        json={"localpart": "alice", "temporary_password": "temporary-password-value", "administrator": False},
    )
    created = client.post(
        "/_hypha/admin/v1/users",
        headers=headers,
        json={"localpart": "alice", "temporary_password": "temporary-password-value", "administrator": False},
    )
    promoted = client.put(
        "/_hypha/admin/v1/users/administrator",
        headers=headers,
        json={"user_id": "@alice:example.org", "administrator": True},
    )
    requests = client.get("/_hypha/admin/v1/password-reset-requests", headers=headers)
    reset = client.put(
        "/_hypha/admin/v1/users/password",
        headers=headers,
        json={
            "user_id": "@alice:example.org",
            "request_id": "11111111-1111-4111-8111-111111111111",
            "temporary_password": "replacement-password-value",
        },
    )

    assert unauthorized.status_code == 401
    assert created.status_code == 201
    assert created.json()["user_id"] == "@alice:example.org"
    assert promoted.status_code == 200
    assert promoted.json()["is_administrator"] is True
    assert requests.status_code == 200
    assert requests.json()[0]["request_id"] == "11111111-1111-4111-8111-111111111111"
    assert reset.status_code == 204
    assert [name for name, _ in admin.calls] == [
        "create_account",
        "set_administrator",
        "password_reset_requests",
        "reset_password",
    ]
    assert admin.calls[0][1] == {
        "localpart": "alice",
        "temporary_password": "temporary-password-value",
        "administrator": False,
    }
    assert admin.calls[3][1]["request_id"] == "11111111-1111-4111-8111-111111111111"


def test_room_and_destructive_account_operations_are_typed_and_session_gated():
    client, admin, token = make_client()
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/_hypha/admin/v1/rooms",
        headers=headers,
        json={"name": "Room", "topic": "Topic", "as_space": False, "visibility": "invite_only"},
    )
    logout = client.post(
        "/_hypha/admin/v1/users/logout",
        headers=headers,
        json={"user_id": "@alice:example.org"},
    )
    deactivate = client.post(
        "/_hypha/admin/v1/users/deactivate",
        headers=headers,
        json={"user_id": "@alice:example.org"},
    )
    purge = client.post(
        "/_hypha/admin/v1/rooms/purge",
        headers=headers,
        json={"room_id": "!room:example.org"},
    )

    assert created.status_code == 201
    assert created.json()["room_id"] == "!room:example.org"
    assert logout.status_code == 204
    assert deactivate.status_code == 204
    assert purge.status_code == 204
    assert [name for name, _ in admin.calls] == [
        "create_room",
        "logout_account",
        "deactivate_account",
        "purge_room",
    ]


def test_operation_payloads_are_strict_and_never_reflect_credentials():
    client, admin, token = make_client()
    headers = {"Authorization": f"Bearer {token}"}
    attempted = "must-not-be-reflected-password-value"

    malformed = client.post(
        "/_hypha/admin/v1/users",
        headers=headers,
        json={
            "localpart": "Alice Invalid",
            "temporary_password": attempted,
            "administrator": False,
            "unexpected": attempted,
        },
    )
    generic = client.post(
        "/_hypha/admin/v1/proxy",
        headers=headers,
        json={"method": "DELETE", "path": "/_synapse/admin/v1/anything", "secret": attempted},
    )

    assert malformed.status_code == 400
    assert malformed.json() == {"error": "invalid request"}
    assert attempted not in malformed.text
    assert generic.status_code == 404
    assert admin.calls == []
