"""Typed server-only Synapse authority adapter for the Hypha broker."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

import httpx

_MAX_UPSTREAM_RESPONSE_BYTES = 1024 * 1024


class SynapseAdminError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("homeserver administration is unavailable")


class SynapseAuthorityRejected(SynapseAdminError):
    """The server-held service identity could no longer authenticate."""


class SynapseTransport(Protocol):
    async def send(self, request: httpx.Request) -> Any: ...


@dataclass(frozen=True)
class _TransportResponse:
    status_code: int
    body: bytes
    response_url: httpx.URL
    headers: dict[str, str]


class HTTPXTransport:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(follow_redirects=False, timeout=30.0)

    async def send(self, request: httpx.Request) -> _TransportResponse:
        response = await self._client.send(request, stream=True)
        body = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_UPSTREAM_RESPONSE_BYTES:
                    raise SynapseAdminError()
        finally:
            await response.aclose()
        return _TransportResponse(
            status_code=response.status_code,
            body=bytes(body),
            response_url=response.url,
            headers=dict(response.headers),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class SynapseAdminAdapterClient:
    """Allowlisted Synapse Admin API operations using server-resident authority."""

    _LOCALPART = re.compile(r"^[a-z0-9._=-]+$")
    _PASSWORD_RESET_TYPE = "ca.zenithresearch.hypha.password_reset_request"
    _MAX_RESPONSE_BYTES = _MAX_UPSTREAM_RESPONSE_BYTES
    _MAX_LIST_ITEMS = 10_000
    _MAX_LIST_PAGES = 100

    def __init__(
        self,
        *,
        homeserver: str,
        service_user_id: str,
        service_password: str,
        transport: SynapseTransport | None = None,
    ) -> None:
        if not self._valid_configuration(homeserver, service_user_id, service_password):
            raise ValueError("invalid Synapse broker configuration")
        self._homeserver = homeserver
        self._service_user_id = service_user_id
        self._service_password = service_password  # private-artifact-scan: allow-variable-flow
        self._transport = transport or HTTPXTransport()
        self._access_token: str | None = None
        self._login_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "SynapseAdminAdapterClient(authority=redacted)"

    async def aclose(self) -> None:
        close = getattr(self._transport, "aclose", None)
        if close is not None:
            await close()

    async def ready(self) -> None:
        response = await self._admin_request(
            "GET",
            f"/_synapse/admin/v2/users/{self._encoded(self._service_user_id)}",
        )
        if response.status_code != 200:
            raise SynapseAdminError()
        payload = self._json_object(response)
        parsed = self._parse_user(payload)
        if (
            parsed["user_id"] != self._service_user_id
            or parsed["is_administrator"] is not True
            or parsed["is_deactivated"] is not False
            or parsed["is_guest"] is not False
            or parsed["user_type"] is not None
            or payload.get("locked", False) is not False
            or payload.get("approved", True) is not True
        ):
            raise SynapseAdminError()

    async def snapshot(self) -> dict[str, object]:
        users = await self._list_users()
        rooms = await self._list_rooms()
        return {"users": users, "rooms": rooms}

    async def create_account(
        self,
        *,
        localpart: str,
        temporary_password: str,
        administrator: bool,
    ) -> dict[str, object]:
        if (
            not isinstance(localpart, str)
            or not self._LOCALPART.fullmatch(localpart)
            or len(localpart) > 255
            or not self._valid_account_password(temporary_password)
            or not isinstance(administrator, bool)
        ):
            raise SynapseAdminError()
        server_name = self._service_user_id.partition(":")[2]
        user_id = f"@{localpart}:{server_name}"
        path = f"/_synapse/admin/v2/users/{self._encoded(user_id)}"
        response = await self._admin_request(
            "PUT",
            path,
            {
                "password": temporary_password,
                "admin": administrator,
                "deactivated": False,
                "approved": True,
            },
        )
        if response.status_code != 200:
            raise SynapseAdminError()
        response_user = self._parse_user(self._json_object(response))
        if (
            response_user["user_id"] != user_id
            or response_user["is_administrator"] is not administrator
            or response_user["is_deactivated"] is not False
        ):
            raise SynapseAdminError()
        verified = await self._account(user_id)
        if (
            verified["admin"] is not administrator
            or verified["deactivated"] is not False
            or verified.get("locked") is True
            or verified.get("approved") is False
            or not isinstance(verified.get("password_hash"), str)
            or not verified["password_hash"]
        ):
            raise SynapseAdminError()
        return self._parse_user(verified)

    async def set_administrator(
        self,
        *,
        user_id: str,
        administrator: bool,
    ) -> dict[str, object]:
        self._validate_mutable_user(user_id)
        if not isinstance(administrator, bool):
            raise SynapseAdminError()
        response = await self._admin_request(
            "PUT",
            f"/_synapse/admin/v1/users/{self._encoded(user_id)}/admin",
            {"admin": administrator},
        )
        if response.status_code != 200:
            raise SynapseAdminError()
        verified = await self._account(user_id)
        if (
            verified["admin"] is not administrator
            or verified["deactivated"] is not False
            or verified.get("locked") is True
            or verified.get("approved") is False
        ):
            raise SynapseAdminError()
        return self._parse_user(verified)

    async def password_reset_requests(self) -> list[dict[str, object]]:
        requests: list[dict[str, object]] = []
        for user in await self._list_users():
            if user["is_deactivated"] is True or user["is_guest"] is True:
                continue
            response = await self._admin_request(
                "GET",
                self._password_reset_path(str(user["user_id"])),
            )
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                raise SynapseAdminError()
            request = self._parse_password_reset(
                self._json_object(response),
                expected_user_id=str(user["user_id"]),
            )
            if request is not None:
                requests.append(request)
        return sorted(requests, key=lambda value: (value["requested_at_ms"], value["user_id"]))

    async def reset_password(
        self,
        *,
        user_id: str,
        request_id: str,
        temporary_password: str,
    ) -> None:
        self._validate_mutable_user(user_id)
        if not self._valid_uuid(request_id) or not self._valid_account_password(temporary_password):
            raise SynapseAdminError()
        request_response = await self._admin_request("GET", self._password_reset_path(user_id))
        if request_response.status_code != 200:
            raise SynapseAdminError()
        pending = self._parse_password_reset(
            self._json_object(request_response),
            expected_user_id=user_id,
        )
        if pending is None or pending["request_id"] != request_id:
            raise SynapseAdminError()
        account = await self._account(user_id)
        previous_hash = account.get("password_hash")
        if (
            account["deactivated"] is not False
            or account.get("locked") is True
            or account.get("approved") is False
            or not isinstance(previous_hash, str)
            or not previous_hash
        ):
            raise SynapseAdminError()
        response = await self._admin_request(
            "PUT",
            f"/_synapse/admin/v2/users/{self._encoded(user_id)}",
            {
                "password": temporary_password,
                "admin": account["admin"],
                "deactivated": False,
                "approved": True,
                "logout_devices": True,
            },
        )
        if response.status_code != 200:
            raise SynapseAdminError()
        verified = await self._account(user_id)
        new_hash = verified.get("password_hash")
        if (
            verified["admin"] is not account["admin"]
            or verified["deactivated"] is not False
            or verified.get("locked") is True
            or verified.get("approved") is False
            or not isinstance(new_hash, str)
            or not new_hash
            or new_hash == previous_hash
        ):
            raise SynapseAdminError()

    async def create_room(
        self,
        *,
        name: str,
        topic: str,
        as_space: bool,
        visibility: str,
    ) -> dict[str, object]:
        clean_name = name.strip() if isinstance(name, str) else ""
        if (
            not clean_name
            or len(clean_name) > 255
            or not isinstance(topic, str)
            or len(topic) > 1_000
            or not isinstance(as_space, bool)
            or visibility not in ("invite_only", "public")
        ):
            raise SynapseAdminError()
        body: dict[str, object] = {
            "name": clean_name,
            "visibility": "public" if visibility == "public" else "private",
            "preset": "public_chat" if visibility == "public" else "private_chat",
        }
        if topic:
            body["topic"] = topic
        if as_space:
            body["creation_content"] = {"type": "m.space"}
        else:
            body["initial_state"] = [
                {
                    "type": "m.room.encryption",
                    "state_key": "",
                    "content": {"algorithm": "m.megolm.v1.aes-sha2"},
                }
            ]
        response = await self._admin_request("POST", "/_matrix/client/v3/createRoom", body)
        if response.status_code != 200:
            raise SynapseAdminError()
        room_id = self._json_object(response).get("room_id")
        if not self._valid_matrix_id(room_id, "!"):
            raise SynapseAdminError()
        return {"room_id": room_id, "name": clean_name, "joined_member_count": 1}

    async def logout_account(self, *, user_id: str) -> None:
        self._validate_mutable_user(user_id)
        response = await self._admin_request(
            "POST",
            f"/_synapse/admin/v1/users/{self._encoded(user_id)}/logout",
            {},
        )
        if response.status_code != 200:
            raise SynapseAdminError()

    async def deactivate_account(self, *, user_id: str) -> None:
        self._validate_mutable_user(user_id)
        response = await self._admin_request(
            "POST",
            f"/_synapse/admin/v1/deactivate/{self._encoded(user_id)}",
            {"erase": True},
        )
        if response.status_code != 200:
            raise SynapseAdminError()

    async def purge_room(self, *, room_id: str) -> None:
        if not self._valid_matrix_id(room_id, "!"):
            raise SynapseAdminError()
        response = await self._admin_request(
            "DELETE",
            f"/_synapse/admin/v2/rooms/{self._encoded(room_id)}",
            {"block": True, "purge": True, "force_purge": True},
        )
        if response.status_code != 200:
            raise SynapseAdminError()

    async def _login(self) -> None:
        body = {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": self._service_user_id},
            "password": self._service_password,
            "refresh_token": False,
            "initial_device_display_name": "Hypha administration broker",
        }
        response = await self._send(
            method="POST",
            path="/_matrix/client/v3/login",
            body=body,
            access_token=None,
        )
        if response.status_code in {401, 403}:
            self._access_token = None
            raise SynapseAuthorityRejected()
        if response.status_code != 200:
            self._access_token = None
            raise SynapseAdminError()
        payload = self._json_object(response)
        token = payload.get("access_token")
        if (
            payload.get("user_id") != self._service_user_id
            or not isinstance(token, str)
            or not self._valid_token(token)
        ):
            self._access_token = None
            raise SynapseAdminError()
        self._access_token = token

    async def _list_users(self) -> list[dict[str, object]]:
        users: list[dict[str, object]] = []
        offset = "0"
        seen_offsets: set[str] = set()
        for _page_number in range(self._MAX_LIST_PAGES):
            if offset in seen_offsets:
                raise SynapseAdminError()
            seen_offsets.add(offset)
            query = urlencode(
                [
                    ("from", offset),
                    ("limit", "100"),
                    ("order_by", "name"),
                    ("dir", "f"),
                ]
            )
            response = await self._admin_request("GET", f"/_synapse/admin/v2/users?{query}")
            if response.status_code != 200:
                raise SynapseAdminError()
            payload = self._json_object(response)
            page = payload.get("users")
            if not isinstance(page, list):
                raise SynapseAdminError()
            for raw_user in page:
                user = self._parse_user(raw_user)
                if user["user_id"] != self._service_user_id:
                    users.append(user)
                    if len(users) > self._MAX_LIST_ITEMS:
                        raise SynapseAdminError()
            next_token = payload.get("next_token")
            if next_token is None:
                return users
            if not isinstance(next_token, str | int) or not str(next_token):
                raise SynapseAdminError()
            offset = str(next_token)
        raise SynapseAdminError()

    async def _list_rooms(self) -> list[dict[str, object]]:
        rooms: list[dict[str, object]] = []
        offset = "0"
        seen_offsets: set[str] = set()
        for _page_number in range(self._MAX_LIST_PAGES):
            if offset in seen_offsets:
                raise SynapseAdminError()
            seen_offsets.add(offset)
            query = urlencode(
                [
                    ("from", offset),
                    ("limit", "100"),
                    ("order_by", "name"),
                    ("dir", "f"),
                ]
            )
            response = await self._admin_request("GET", f"/_synapse/admin/v1/rooms?{query}")
            if response.status_code != 200:
                raise SynapseAdminError()
            payload = self._json_object(response)
            page = payload.get("rooms")
            if not isinstance(page, list):
                raise SynapseAdminError()
            rooms.extend(self._parse_room(raw_room) for raw_room in page)
            if len(rooms) > self._MAX_LIST_ITEMS:
                raise SynapseAdminError()
            next_token = payload.get("next_batch")
            if next_token is None:
                return rooms
            if not isinstance(next_token, str | int) or not str(next_token):
                raise SynapseAdminError()
            offset = str(next_token)
        raise SynapseAdminError()

    async def _admin_request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
    ) -> Any:
        access_token = await self._ensure_access_token()
        response = await self._send(
            method=method,
            path=path,
            body=body,
            access_token=access_token,  # private-artifact-scan: allow-variable-flow
        )
        if response.status_code != 401:
            return response
        await self._invalidate_access_token(access_token)
        replacement_token = await self._ensure_access_token()
        retry = await self._send(
            method=method,
            path=path,
            body=body,
            access_token=replacement_token,
        )
        if retry.status_code == 401:
            await self._invalidate_access_token(replacement_token)
            raise SynapseAuthorityRejected()
        return retry

    async def _ensure_access_token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        async with self._login_lock:
            if self._access_token is None:
                await self._login()
            if self._access_token is None:  # pragma: no cover - defensive invariant
                raise SynapseAdminError()
            return self._access_token

    async def _invalidate_access_token(self, rejected_token: str | None) -> None:
        if rejected_token is None:
            return
        async with self._login_lock:
            if self._access_token == rejected_token:
                self._access_token = None

    async def _send(
        self,
        *,
        method: str,
        path: str,
        body: dict[str, object] | None,
        access_token: str | None,
    ) -> Any:
        headers = {"accept": "application/json"}
        content = None
        if body is not None:
            headers["content-type"] = "application/json"
            content = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        if access_token is not None:
            headers["authorization"] = f"Bearer {access_token}"
        request = httpx.Request(
            method=method,
            url=self._homeserver + path,
            headers=headers,
            content=content,
        )
        try:
            response = await self._transport.send(request)
        except Exception as exc:
            raise SynapseAdminError() from exc
        response_url = self._response_url(response)
        if not self._same_origin(request.url, response_url):
            await self._invalidate_access_token(access_token)
            raise SynapseAdminError()
        return response

    def _json_object(self, response: Any) -> dict[str, object]:
        headers = getattr(response, "headers", None)
        try:
            content_type = headers.get("content-type", "")
        except (AttributeError, TypeError):
            content_type = ""
        if content_type.partition(";")[0].strip().lower() != "application/json":
            raise SynapseAdminError()
        try:
            payload = json.loads(self._response_body(response))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SynapseAdminError() from exc
        if not isinstance(payload, dict):
            raise SynapseAdminError()
        return payload

    def _parse_user(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise SynapseAdminError()
        user_id = value.get("name")
        administrator = value.get("admin")
        deactivated = value.get("deactivated")
        guest = value.get("is_guest")
        user_type = value.get("user_type")
        if (
            not self._valid_matrix_id(user_id, "@")
            or not isinstance(administrator, bool)
            or not isinstance(deactivated, bool)
            or not isinstance(guest, bool)
            or (user_type is not None and not isinstance(user_type, str))
        ):
            raise SynapseAdminError()
        return {
            "user_id": user_id,
            "is_administrator": administrator,
            "is_deactivated": deactivated,
            "is_guest": guest,
            "user_type": user_type,
        }

    def _parse_room(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise SynapseAdminError()
        room_id = value.get("room_id")
        name = value.get("name")
        members = value.get("joined_members")
        if (
            not self._valid_matrix_id(room_id, "!")
            or (name is not None and not isinstance(name, str))
            or not isinstance(members, int)
            or isinstance(members, bool)
            or members < 0
        ):
            raise SynapseAdminError()
        return {
            "room_id": room_id,
            "name": name or room_id,
            "joined_member_count": members,
        }

    async def _account(self, user_id: str) -> dict[str, object]:
        response = await self._admin_request(
            "GET",
            f"/_synapse/admin/v2/users/{self._encoded(user_id)}",
        )
        if response.status_code != 200:
            raise SynapseAdminError()
        payload = self._json_object(response)
        parsed = self._parse_user(payload)
        if parsed["user_id"] != user_id:
            raise SynapseAdminError()
        return payload

    def _parse_password_reset(
        self,
        payload: dict[str, object],
        *,
        expected_user_id: str,
    ) -> dict[str, object] | None:
        if payload.get("status") != "pending":
            return None
        request_id = payload.get("request_id")
        requested_at = payload.get("requested_at_ms")
        if (
            not isinstance(request_id, str)
            or not self._valid_uuid(request_id)
            or not isinstance(requested_at, int)
            or isinstance(requested_at, bool)
            or requested_at <= 0
        ):
            raise SynapseAdminError()
        return {
            "user_id": expected_user_id,
            "request_id": request_id,
            "requested_at_ms": requested_at,
        }

    def _validate_mutable_user(self, user_id: str) -> None:
        if not self._valid_matrix_id(user_id, "@") or user_id == self._service_user_id:
            raise SynapseAdminError()

    def _password_reset_path(self, user_id: str) -> str:
        return (
            f"/_synapse/admin/v1/users/{self._encoded(user_id)}/accountdata/"
            f"{self._PASSWORD_RESET_TYPE}"
        )

    @staticmethod
    def _encoded(value: str) -> str:
        return quote(value, safe="")

    @staticmethod
    def _valid_uuid(value: str) -> bool:
        try:
            return str(UUID(value)) == value.lower()
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _valid_configuration(homeserver: str, user_id: str, password: str) -> bool:
        parsed = urlsplit(homeserver)
        return (
            parsed.scheme == "http"
            and parsed.hostname == "matrix-synapse"
            and parsed.port == 8008
            and not parsed.username
            and not parsed.password
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
            and SynapseAdminAdapterClient._valid_matrix_id(user_id, "@")
            and user_id.split(":", 1)[0] == "@_hypha_admin_broker"
            and SynapseAdminAdapterClient._valid_secret(password)
        )

    @staticmethod
    def _valid_secret(value: str) -> bool:
        if not isinstance(value, str):
            return False
        encoded = value.encode("utf-8", errors="ignore")
        return (
            32 <= len(encoded) <= 512
            and not any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        )

    @staticmethod
    def _valid_account_password(value: str) -> bool:
        if not isinstance(value, str):
            return False
        encoded = value.encode("utf-8", errors="ignore")
        return (
            12 <= len(encoded) <= 512
            and not any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        )

    @staticmethod
    def _valid_token(value: str) -> bool:
        try:
            encoded = value.encode("ascii")
        except (AttributeError, UnicodeEncodeError):
            return False
        return 32 <= len(encoded) <= 4096 and not any(byte < 0x21 or byte == 0x7F for byte in encoded)

    @staticmethod
    def _valid_matrix_id(value: object, sigil: str) -> bool:
        if not isinstance(value, str) or not value.startswith(sigil) or len(value) > 512:
            return False
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            return False
        localpart, separator, server_name = value.partition(":")
        return bool(separator and len(localpart) > 1 and server_name)

    @staticmethod
    def _response_body(response: Any) -> bytes:
        body = getattr(response, "body", None)
        if body is None:
            body = getattr(response, "content", None)
        if not isinstance(body, bytes):
            raise SynapseAdminError()
        if len(body) > SynapseAdminAdapterClient._MAX_RESPONSE_BYTES:
            raise SynapseAdminError()
        return body

    @staticmethod
    def _response_url(response: Any) -> httpx.URL:
        raw = getattr(response, "response_url", None)
        if raw is None:
            raw = getattr(response, "url", None)
        try:
            return httpx.URL(raw)
        except (TypeError, ValueError) as exc:
            raise SynapseAdminError() from exc

    @staticmethod
    def _same_origin(request_url: httpx.URL, response_url: httpx.URL) -> bool:
        return (
            request_url.scheme == response_url.scheme
            and request_url.host == response_url.host
            and request_url.port == response_url.port
        )
