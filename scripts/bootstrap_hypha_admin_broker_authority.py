#!/usr/bin/env python3
"""Create or verify the one hidden Synapse administrator used by the Hypha broker."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from scripts.provision_matrix_admins import build_registration_mac

SERVICE_LOCALPART = "_hypha_admin_broker"
INTERNAL_SYNAPSE_ORIGIN = "http://matrix-synapse:8008"
MAX_RESPONSE_BYTES = 256 * 1024

HttpClient = Callable[
    [str, str, dict[str, Any] | None, str | None],
    tuple[int, Any],
]


class AuthorityBootstrapError(RuntimeError):
    """A fail-closed error that contains no credential or upstream response data."""


def _valid_secret(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return 32 <= len(encoded) <= 512 and not any(byte < 0x20 or byte == 0x7F for byte in encoded)


def service_user_id(server_name: str) -> str:
    if not isinstance(server_name, str) or not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+",
        server_name,
    ):
        raise AuthorityBootstrapError("broker service authority configuration is invalid")
    return f"@{SERVICE_LOCALPART}:{server_name}"


def _login(
    *,
    user_id: str,
    password: str,
    http: HttpClient,
) -> tuple[int, str | None]:
    status, payload = http(
        "POST",
        INTERNAL_SYNAPSE_ORIGIN + "/_matrix/client/v3/login",
        {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": user_id},
            "password": password,
            "refresh_token": False,
            "initial_device_display_name": "Hypha administration broker bootstrap",
        },
        None,
    )
    if status in {401, 403, 404}:
        return status, None
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if (
        status != 200
        or not isinstance(payload, dict)
        or payload.get("user_id") != user_id
        or not isinstance(token, str)
        or not 32 <= len(token.encode("utf-8", errors="ignore")) <= 4096
        or any(ord(character) < 0x21 or ord(character) == 0x7F for character in token)
    ):
        raise AuthorityBootstrapError("broker service authority login failed")
    return status, token


def _register(
    *,
    user_id: str,
    registration_secret: str,
    service_password: str,
    http: HttpClient,
) -> None:
    nonce_status, nonce_payload = http(
        "GET",
        INTERNAL_SYNAPSE_ORIGIN + "/_synapse/admin/v1/register",
        None,
        None,
    )
    nonce = nonce_payload.get("nonce") if isinstance(nonce_payload, dict) else None
    if nonce_status != 200 or not isinstance(nonce, str) or not nonce:
        raise AuthorityBootstrapError("broker service authority registration failed")
    localpart = user_id.removeprefix("@").split(":", 1)[0]
    body = {
        "nonce": nonce,
        "username": localpart,
        "password": service_password,
        "admin": True,
        "mac": build_registration_mac(
            secret=registration_secret,
            nonce=nonce,
            username=localpart,
            credential=service_password,
            admin=True,
        ),
    }
    status, payload = http(
        "POST",
        INTERNAL_SYNAPSE_ORIGIN + "/_synapse/admin/v1/register",
        body,
        None,
    )
    if status != 200 or not isinstance(payload, dict) or payload.get("user_id") != user_id:
        raise AuthorityBootstrapError("broker service authority registration failed")


def _verify_account(*, user_id: str, access_token: str, http: HttpClient) -> None:
    encoded = urllib.parse.quote(user_id, safe="")
    status, payload = http(
        "GET",
        INTERNAL_SYNAPSE_ORIGIN + f"/_synapse/admin/v2/users/{encoded}",
        None,
        access_token,
    )
    if (
        status != 200
        or not isinstance(payload, dict)
        or payload.get("name") != user_id
        or payload.get("admin") is not True
        or payload.get("deactivated") is not False
        or payload.get("is_guest") is not False
        or payload.get("user_type") is not None
        or payload.get("locked", False) is not False
        or payload.get("approved", True) is not True
    ):
        raise AuthorityBootstrapError("broker service authority verification failed")


def bootstrap_authority(
    *,
    registration_secret: str,
    service_password: str,
    server_name: str,
    http: HttpClient,
) -> dict[str, str]:
    if not _valid_secret(registration_secret) or not _valid_secret(service_password):
        raise AuthorityBootstrapError("broker service authority configuration is invalid")
    user_id = service_user_id(server_name)
    status, access_token = _login(user_id=user_id, password=service_password, http=http)
    result = "verified"
    if status != 200:
        _register(
            user_id=user_id,
            registration_secret=registration_secret,
            service_password=service_password,
            http=http,
        )
        status, access_token = _login(user_id=user_id, password=service_password, http=http)
        result = "created"
    if status != 200 or access_token is None:
        raise AuthorityBootstrapError("broker service authority login failed")
    _verify_account(user_id=user_id, access_token=access_token, http=http)
    return {"service_user_id": user_id, "status": result}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # type: ignore[no-untyped-def]
        return None


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    access_token: str | None,
) -> tuple[int, Any]:
    if not url.startswith(INTERNAL_SYNAPSE_ORIGIN + "/"):
        raise AuthorityBootstrapError("broker service authority request failed")
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode() if payload is not None else None
    headers = {"accept": "application/json"}
    if data is not None:
        headers["content-type"] = "application/json"
    if access_token is not None:
        headers["authorization"] = "Bearer " + access_token
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=15)  # noqa: S310
    except urllib.error.HTTPError as exc:
        response = exc
    except (OSError, TimeoutError) as exc:
        raise AuthorityBootstrapError("broker service authority request failed") from exc
    try:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        status = response.status
        final_url = response.geturl()
        content_type = response.headers.get_content_type()
    finally:
        response.close()
    if len(body) > MAX_RESPONSE_BYTES or final_url != url:
        raise AuthorityBootstrapError("broker service authority response was invalid")
    if not body:
        parsed: Any = {}
    elif content_type != "application/json":
        raise AuthorityBootstrapError("broker service authority response was invalid")
    else:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AuthorityBootstrapError("broker service authority response was invalid") from exc
    return status, parsed


def main() -> int:
    try:
        result = bootstrap_authority(
            registration_secret=os.environ.get("REGISTRATION_SHARED_SECRET", ""),
            service_password=os.environ.get("HYPHA_ADMIN_BROKER_SERVICE_PASSWORD", ""),
            server_name=os.environ.get("MATRIX_SERVER_NAME", ""),
            http=http_json,
        )
    except AuthorityBootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
