from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bootstrap_hypha_admin_broker_authority.py"
REGISTRATION_SECRET = "registration-shared-secret-value-123456"
SERVICE_PASSWORD = "server-only-service-password-value-1234"
ACCESS_TOKEN = "server-only-access-token-value-12345678"
USER_ID = "@_hypha_admin_broker:example.org"


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap_hypha_admin_broker_authority", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HTTP:
    def __init__(self, responses: list[tuple[int, Any]]):
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, Any] | None, str | None]] = []

    def __call__(self, method: str, url: str, body: dict[str, Any] | None, token: str | None):
        self.requests.append((method, url, body, token))
        return self.responses.pop(0)


def account(*, admin: bool = True, name: str = USER_ID) -> dict[str, Any]:
    return {
        "name": name,
        "admin": admin,
        "deactivated": False,
        "is_guest": False,
        "user_type": None,
        "locked": False,
        "approved": True,
    }


def test_first_run_registers_only_the_exact_service_admin_then_verifies_login_and_role():
    bootstrap = load_bootstrap()
    http = HTTP(
        [
            (403, {"errcode": "M_FORBIDDEN"}),
            (200, {"nonce": "registration-nonce"}),
            (200, {"user_id": USER_ID}),
            (200, {"user_id": USER_ID, "access_token": ACCESS_TOKEN}),
            (200, account()),
        ]
    )

    result = bootstrap.bootstrap_authority(
        registration_secret=REGISTRATION_SECRET,
        service_password=SERVICE_PASSWORD,
        server_name="example.org",
        http=http,
    )

    assert result == {"service_user_id": USER_ID, "status": "created"}
    assert [request[0] for request in http.requests] == ["POST", "GET", "POST", "POST", "GET"]
    registration = http.requests[2][2]
    assert registration is not None
    assert registration["username"] == "_hypha_admin_broker"
    assert registration["admin"] is True
    assert registration["password"] == SERVICE_PASSWORD
    assert registration["mac"] == bootstrap.build_registration_mac(
        secret=REGISTRATION_SECRET,
        nonce="registration-nonce",
        username="_hypha_admin_broker",
        credential=SERVICE_PASSWORD,
        admin=True,
    )
    assert http.requests[-1][3] == ACCESS_TOKEN
    rendered = repr(result)
    for secret in [REGISTRATION_SECRET, SERVICE_PASSWORD, ACCESS_TOKEN]:
        assert secret not in rendered


def test_existing_exact_authority_is_verified_without_registration():
    bootstrap = load_bootstrap()
    http = HTTP(
        [
            (200, {"user_id": USER_ID, "access_token": ACCESS_TOKEN}),
            (200, account()),
        ]
    )

    result = bootstrap.bootstrap_authority(
        registration_secret=REGISTRATION_SECRET,
        service_password=SERVICE_PASSWORD,
        server_name="example.org",
        http=http,
    )

    assert result["status"] == "verified"
    assert [request[0] for request in http.requests] == ["POST", "GET"]


@pytest.mark.parametrize(
    "bad_account",
    [
        account(admin=False),
        account(name="@ordinary:example.org"),
        dict(account(), deactivated=True),
        dict(account(), is_guest=True),
        dict(account(), user_type="support"),
        dict(account(), locked=True),
        dict(account(), approved=False),
    ],
)
def test_identity_or_role_mismatch_fails_closed_without_exposing_authority(bad_account):
    bootstrap = load_bootstrap()
    http = HTTP(
        [
            (200, {"user_id": USER_ID, "access_token": ACCESS_TOKEN}),
            (200, bad_account),
        ]
    )

    with pytest.raises(bootstrap.AuthorityBootstrapError) as captured:
        bootstrap.bootstrap_authority(
            registration_secret=REGISTRATION_SECRET,
            service_password=SERVICE_PASSWORD,
            server_name="example.org",
            http=http,
        )

    assert str(captured.value) == "broker service authority verification failed"
    assert ACCESS_TOKEN not in repr(captured.value)
    assert SERVICE_PASSWORD not in repr(captured.value)


def test_wrong_existing_service_password_cannot_fall_through_as_success():
    bootstrap = load_bootstrap()
    http = HTTP(
        [
            (403, {"errcode": "M_FORBIDDEN"}),
            (200, {"nonce": "registration-nonce"}),
            (400, {"errcode": "M_USER_IN_USE"}),
        ]
    )

    with pytest.raises(bootstrap.AuthorityBootstrapError, match="registration failed"):
        bootstrap.bootstrap_authority(
            registration_secret=REGISTRATION_SECRET,
            service_password=SERVICE_PASSWORD,
            server_name="example.org",
            http=http,
        )
