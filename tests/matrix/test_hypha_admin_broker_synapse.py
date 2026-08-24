from __future__ import annotations

import asyncio
import json

import pytest

from services.hypha_admin_broker.synapse import SynapseAdminAdapterClient, SynapseAdminError

SERVICE_USER = "@_hypha_admin_broker:example.org"
SERVICE_PASSWORD = "server-only-service-password-value"  # private-artifact-scan: allow-test-fixture
ACCESS_TOKEN = "server-only-synapse-access-token-value"  # private-artifact-scan: allow-test-fixture


class Response:
    def __init__(
        self,
        status: int,
        body: dict[str, object],
        *,
        url: str = "http://matrix-synapse:8008/_matrix/client/v3/login",
    ):
        self.status_code = status
        self.body = json.dumps(body).encode()
        self.response_url = url
        self.headers = {"content-type": "application/json"}


class Transport:
    def __init__(self, responses: list[Response]):
        self.responses = responses
        self.requests = []

    async def send(self, request):
        self.requests.append(request)
        if not self.responses:
            raise OSError("offline")
        return self.responses.pop(0)


class ConcurrentAuthorityTransport:
    def __init__(self):
        self.requests = []
        self.login_calls = 0

    async def send(self, request):
        self.requests.append(request)
        if request.url.path == "/_matrix/client/v3/login":
            self.login_calls += 1
            await asyncio.sleep(0.01)
            return Response(
                200,
                {"user_id": SERVICE_USER, "access_token": ACCESS_TOKEN},
                url=str(request.url),
            )
        return Response(
            200,
            {
                "name": SERVICE_USER,
                "admin": True,
                "deactivated": False,
                "is_guest": False,
                "user_type": None,
                "locked": False,
                "approved": True,
            },
            url=str(request.url),
        )


class ConcurrentReauthenticationTransport(ConcurrentAuthorityTransport):
    def __init__(self):
        super().__init__()
        self.old_token_requests = 0
        self.both_old_token_requests_started = asyncio.Event()

    async def send(self, request):
        self.requests.append(request)
        if request.url.path == "/_matrix/client/v3/login":
            self.login_calls += 1
            token = ACCESS_TOKEN if self.login_calls == 1 else "replacement-access-token-value-123456"
            return Response(
                200,
                {"user_id": SERVICE_USER, "access_token": token},
                url=str(request.url),
            )
        if request.headers.get("authorization") == f"Bearer {ACCESS_TOKEN}":
            self.old_token_requests += 1
            if self.old_token_requests == 2:
                self.both_old_token_requests_started.set()
            await self.both_old_token_requests_started.wait()
            return Response(401, {}, url=str(request.url))
        return Response(
            200,
            {
                "name": SERVICE_USER,
                "admin": True,
                "deactivated": False,
                "is_guest": False,
                "user_type": None,
            },
            url=str(request.url),
        )


def make_client(transport: Transport) -> SynapseAdminAdapterClient:
    return SynapseAdminAdapterClient(
        homeserver="http://matrix-synapse:8008",
        service_user_id=SERVICE_USER,
        service_password=SERVICE_PASSWORD,
        transport=transport,
    )


def test_snapshot_logs_in_server_side_then_uses_typed_admin_paths_and_hides_service_user():
    transport = Transport(
        [
            Response(200, {"user_id": SERVICE_USER, "access_token": ACCESS_TOKEN}),
            Response(
                200,
                {
                    "users": [
                        {
                            "name": SERVICE_USER,
                            "admin": True,
                            "deactivated": False,
                            "is_guest": False,
                            "user_type": None,
                        },
                        {
                            "name": "@alice:example.org",
                            "admin": False,
                            "deactivated": False,
                            "is_guest": False,
                            "user_type": None,
                        },
                    ],
                    "total": 2,
                },
                url="http://matrix-synapse:8008/_synapse/admin/v2/users",
            ),
            Response(
                200,
                {
                    "rooms": [
                        {
                            "room_id": "!room:example.org",
                            "name": "Room",
                            "joined_members": 1,
                        }
                    ],
                    "total_rooms": 1,
                },
                url="http://matrix-synapse:8008/_synapse/admin/v1/rooms",
            ),
        ]
    )
    client = make_client(transport)

    snapshot = asyncio.run(client.snapshot())

    assert snapshot == {
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
    assert [request.method for request in transport.requests] == ["POST", "GET", "GET"]
    assert [request.url for request in transport.requests] == [
        "http://matrix-synapse:8008/_matrix/client/v3/login",
        "http://matrix-synapse:8008/_synapse/admin/v2/users?from=0&limit=100&order_by=name&dir=f",
        "http://matrix-synapse:8008/_synapse/admin/v1/rooms?from=0&limit=100&order_by=name&dir=f",
    ]
    login = json.loads(transport.requests[0].content)
    assert login == {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": SERVICE_USER},
        "password": SERVICE_PASSWORD,
        "refresh_token": False,
        "initial_device_display_name": "Hypha administration broker",
    }
    assert "authorization" not in transport.requests[0].headers
    assert transport.requests[1].headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert transport.requests[2].headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert SERVICE_PASSWORD not in repr(client)
    assert ACCESS_TOKEN not in repr(client)


def test_readiness_proves_exact_service_authority_and_concurrent_probes_share_one_login():
    transport = ConcurrentAuthorityTransport()
    client = make_client(transport)  # type: ignore[arg-type]

    async def probe_twice() -> None:
        await asyncio.gather(client.ready(), client.ready())

    asyncio.run(probe_twice())

    assert transport.login_calls == 1
    assert [request.method for request in transport.requests].count("POST") == 1
    authority_requests = [
        request for request in transport.requests if request.url.path.startswith("/_synapse/admin/v2/users/")
    ]
    assert len(authority_requests) == 2
    assert all(request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}" for request in authority_requests)


def test_concurrent_unauthorized_calls_share_one_reauthentication_without_token_clobbering():
    transport = ConcurrentReauthenticationTransport()
    client = make_client(transport)  # type: ignore[arg-type]

    async def establish_then_probe_twice() -> None:
        await client._ensure_access_token()  # type: ignore[attr-defined]
        await asyncio.gather(client.ready(), client.ready())

    asyncio.run(establish_then_probe_twice())

    assert transport.old_token_requests == 2
    assert transport.login_calls == 2
    replacement = "Bearer replacement-access-token-value-123456"
    assert [request.headers.get("authorization") for request in transport.requests].count(replacement) == 2


def test_readiness_rejects_authority_without_exact_active_admin_postconditions():
    invalid_authority = {
        "name": SERVICE_USER,
        "admin": False,
        "deactivated": False,
        "is_guest": False,
        "user_type": None,
    }
    transport = Transport(
        [
            Response(200, {"user_id": SERVICE_USER, "access_token": ACCESS_TOKEN}),
            Response(
                200,
                invalid_authority,
                url="http://matrix-synapse:8008/_synapse/admin/v2/users/%40_hypha_admin_broker%3Aexample.org",
            ),
        ]
    )

    with pytest.raises(SynapseAdminError, match="homeserver administration is unavailable"):
        asyncio.run(make_client(transport).ready())


def test_adapter_accepts_only_exact_internal_synapse_origin_and_service_identity():
    invalid = [
        "https://matrix-synapse:8008",
        "http://localhost:8008",
        "http://127.0.0.1:8008",
        "http://matrix-synapse",
        "http://matrix-synapse:8008/path",
        "http://user@matrix-synapse:8008",
        "http://matrix-synapse:8008?token=value",
    ]
    for homeserver in invalid:
        with pytest.raises(ValueError, match="invalid Synapse broker configuration"):
            SynapseAdminAdapterClient(
                homeserver=homeserver,
                service_user_id=SERVICE_USER,
                service_password=SERVICE_PASSWORD,
                transport=Transport([]),
            )

    for user_id in ["admin", "@admin:other.example", "@_hypha_admin_broker:example.org\n"]:
        with pytest.raises(ValueError, match="invalid Synapse broker configuration"):
            SynapseAdminAdapterClient(
                homeserver="http://matrix-synapse:8008",
                service_user_id=user_id,
                service_password=SERVICE_PASSWORD,
                transport=Transport([]),
            )


def test_wrong_login_identity_or_redirect_fails_without_retaining_authority():
    for response in [
        Response(200, {"user_id": "@other:example.org", "access_token": ACCESS_TOKEN}),
        Response(
            200,
            {"user_id": SERVICE_USER, "access_token": ACCESS_TOKEN},
            url="http://attacker.invalid/_matrix/client/v3/login",
        ),
    ]:
        client = make_client(Transport([response]))
        with pytest.raises(SynapseAdminError, match="homeserver administration is unavailable"):
            asyncio.run(client.snapshot())
        assert ACCESS_TOKEN not in repr(client)


def test_upstream_json_must_be_bounded_and_have_json_content_type():
    wrong_type = Response(200, {"user_id": SERVICE_USER, "access_token": ACCESS_TOKEN})
    wrong_type.headers = {"content-type": "text/html"}
    oversized = Response(200, {"user_id": SERVICE_USER, "access_token": "x" * (1024 * 1024)})

    for response in [wrong_type, oversized]:
        client = make_client(Transport([response]))
        with pytest.raises(SynapseAdminError, match="homeserver administration is unavailable"):
            asyncio.run(client.snapshot())


def test_unauthorized_admin_call_drops_token_reauthenticates_once_and_never_logs_secrets():
    transport = Transport(
        [
            Response(200, {"user_id": SERVICE_USER, "access_token": ACCESS_TOKEN}),
            Response(401, {}, url="http://matrix-synapse:8008/_synapse/admin/v2/users"),
            Response(200, {"user_id": SERVICE_USER, "access_token": "replacement-access-token-value-123456"}),
            Response(
                200,
                {"users": [], "total": 0},
                url="http://matrix-synapse:8008/_synapse/admin/v2/users",
            ),
            Response(
                200,
                {"rooms": [], "total_rooms": 0},
                url="http://matrix-synapse:8008/_synapse/admin/v1/rooms",
            ),
        ]
    )
    client = make_client(transport)

    assert asyncio.run(client.snapshot()) == {"users": [], "rooms": []}
    assert [request.method for request in transport.requests] == ["POST", "GET", "POST", "GET", "GET"]
    assert ACCESS_TOKEN not in repr(client)
    assert SERVICE_PASSWORD not in repr(client)


def test_typed_mutations_use_allowlisted_paths_and_verify_security_postconditions():
    alice = {
        "name": "@alice:example.org",
        "admin": False,
        "deactivated": False,
        "is_guest": False,
        "user_type": None,
        "locked": False,
        "approved": True,
        "password_hash": "hash-one",
    }
    promoted = dict(alice, admin=True)
    transport = Transport(
        [
            Response(200, {"user_id": SERVICE_USER, "access_token": ACCESS_TOKEN}),
            Response(200, alice, url="http://matrix-synapse:8008/_synapse/admin/v2/users/%40alice%3Aexample.org"),
            Response(200, alice, url="http://matrix-synapse:8008/_synapse/admin/v2/users/%40alice%3Aexample.org"),
            Response(200, {}, url="http://matrix-synapse:8008/_synapse/admin/v1/users/%40alice%3Aexample.org/admin"),
            Response(200, promoted, url="http://matrix-synapse:8008/_synapse/admin/v2/users/%40alice%3Aexample.org"),
            Response(200, {"room_id": "!created:example.org"}, url="http://matrix-synapse:8008/_matrix/client/v3/createRoom"),
            Response(200, {}, url="http://matrix-synapse:8008/_synapse/admin/v1/users/%40alice%3Aexample.org/logout"),
            Response(200, {}, url="http://matrix-synapse:8008/_synapse/admin/v1/deactivate/%40alice%3Aexample.org"),
            Response(200, {}, url="http://matrix-synapse:8008/_synapse/admin/v2/rooms/%21created%3Aexample.org"),
        ]
    )
    client = make_client(transport)

    created = asyncio.run(
        client.create_account(
            localpart="alice",
            temporary_password="temporary-password-value",
            administrator=False,
        )
    )
    updated = asyncio.run(client.set_administrator(user_id="@alice:example.org", administrator=True))
    room = asyncio.run(
        client.create_room(
            name="Operations",
            topic="Private work",
            as_space=False,
            visibility="invite_only",
        )
    )
    asyncio.run(client.logout_account(user_id="@alice:example.org"))
    asyncio.run(client.deactivate_account(user_id="@alice:example.org"))
    asyncio.run(client.purge_room(room_id="!created:example.org"))

    assert created["user_id"] == "@alice:example.org"
    assert updated["is_administrator"] is True
    assert room == {"room_id": "!created:example.org", "name": "Operations", "joined_member_count": 1}
    requests = transport.requests
    assert [request.method for request in requests] == [
        "POST",
        "PUT",
        "GET",
        "PUT",
        "GET",
        "POST",
        "POST",
        "POST",
        "DELETE",
    ]
    create_body = json.loads(requests[1].content)
    assert create_body == {
        "admin": False,
        "approved": True,
        "deactivated": False,
        "password": "temporary-password-value",
    }
    room_body = json.loads(requests[5].content)
    assert room_body["visibility"] == "private"
    assert room_body["preset"] == "private_chat"
    assert room_body["initial_state"][0]["type"] == "m.room.encryption"
    assert json.loads(requests[8].content) == {"block": True, "force_purge": True, "purge": True}


def test_password_reset_requires_exact_pending_request_and_changed_password_hash():
    request_id = "11111111-1111-4111-8111-111111111111"
    account = {
        "name": "@alice:example.org",
        "admin": False,
        "deactivated": False,
        "is_guest": False,
        "user_type": None,
        "locked": False,
        "approved": True,
        "password_hash": "old-hash",
    }
    changed = dict(account, password_hash="new-hash")
    account_data_url = (
        "http://matrix-synapse:8008/_synapse/admin/v1/users/%40alice%3Aexample.org/accountdata/"
        "ca.zenithresearch.hypha.password_reset_request"
    )
    transport = Transport(
        [
            Response(200, {"user_id": SERVICE_USER, "access_token": ACCESS_TOKEN}),
            Response(
                200,
                {"users": [account], "total": 1},
                url="http://matrix-synapse:8008/_synapse/admin/v2/users?from=0&limit=100&order_by=name&dir=f",
            ),
            Response(
                200,
                {"status": "pending", "request_id": request_id, "requested_at_ms": 1_786_000_000_000},
                url=account_data_url,
            ),
            Response(
                200,
                {"status": "pending", "request_id": request_id, "requested_at_ms": 1_786_000_000_000},
                url=account_data_url,
            ),
            Response(200, account, url="http://matrix-synapse:8008/_synapse/admin/v2/users/%40alice%3Aexample.org"),
            Response(200, account, url="http://matrix-synapse:8008/_synapse/admin/v2/users/%40alice%3Aexample.org"),
            Response(200, changed, url="http://matrix-synapse:8008/_synapse/admin/v2/users/%40alice%3Aexample.org"),
        ]
    )
    client = make_client(transport)

    pending = asyncio.run(client.password_reset_requests())
    asyncio.run(
        client.reset_password(
            user_id="@alice:example.org",
            request_id=request_id,
            temporary_password="replacement-password-value",
        )
    )

    assert pending == [
        {
            "user_id": "@alice:example.org",
            "request_id": request_id,
            "requested_at_ms": 1_786_000_000_000,
        }
    ]
    reset_body = json.loads(transport.requests[5].content)
    assert reset_body == {
        "admin": False,
        "approved": True,
        "deactivated": False,
        "logout_devices": True,
        "password": "replacement-password-value",
    }


def test_mutations_refuse_to_target_the_service_authority_before_network():
    transport = Transport([])
    client = make_client(transport)

    for operation in [
        client.set_administrator(user_id=SERVICE_USER, administrator=False),
        client.reset_password(
            user_id=SERVICE_USER,
            request_id="11111111-1111-4111-8111-111111111111",
            temporary_password="replacement-password-value",
        ),
        client.logout_account(user_id=SERVICE_USER),
        client.deactivate_account(user_id=SERVICE_USER),
    ]:
        with pytest.raises(SynapseAdminError, match="homeserver administration is unavailable"):
            asyncio.run(operation)
    assert transport.requests == []
