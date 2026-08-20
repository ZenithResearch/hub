"""Fail-closed typed HTTP façade for Hypha homeserver administration."""

from __future__ import annotations

from typing import Literal, Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth import (
    AuthenticationRejected,
    BrokerSessionStore,
    RateLimited,
    SessionCapacityExceeded,
)
from .synapse import SynapseAuthorityRejected

_API_PREFIX = "/_hypha/admin/v1"
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024


class SynapseAdminAdapter(Protocol):
    async def snapshot(self) -> dict[str, object]: ...
    async def create_account(self, **payload: object) -> dict[str, object]: ...
    async def set_administrator(self, **payload: object) -> dict[str, object]: ...
    async def password_reset_requests(self) -> list[dict[str, object]]: ...
    async def reset_password(self, **payload: object) -> None: ...
    async def create_room(self, **payload: object) -> dict[str, object]: ...
    async def logout_account(self, **payload: object) -> None: ...
    async def deactivate_account(self, **payload: object) -> None: ...
    async def purge_room(self, **payload: object) -> None: ...


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    secret: str


class UserPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: str = Field(min_length=3, max_length=512)
    is_administrator: bool
    is_deactivated: bool
    is_guest: bool
    user_type: str | None


class RoomPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    room_id: str = Field(min_length=3, max_length=512)
    name: str = Field(max_length=255)
    joined_member_count: int = Field(ge=0)


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    users: list[UserPayload] = Field(max_length=10_000)
    rooms: list[RoomPayload] = Field(max_length=10_000)


class CreateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    localpart: str = Field(min_length=1, max_length=255, pattern=r"^[a-z0-9._=-]+$")
    temporary_password: str = Field(
        min_length=12,
        max_length=512,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    administrator: bool


class SetAdministratorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: str = Field(
        min_length=3,
        max_length=512,
        pattern=r"^@[^\x00-\x1f\x7f:]+:[^\x00-\x1f\x7f]+$",
    )
    administrator: bool


class PasswordResetRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: str = Field(
        min_length=3,
        max_length=512,
        pattern=r"^@[^\x00-\x1f\x7f:]+:[^\x00-\x1f\x7f]+$",
    )
    request_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    requested_at_ms: int = Field(gt=0)


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: str = Field(
        min_length=3,
        max_length=512,
        pattern=r"^@[^\x00-\x1f\x7f:]+:[^\x00-\x1f\x7f]+$",
    )
    request_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    temporary_password: str = Field(
        min_length=12,
        max_length=512,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )


class CreateRoomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    topic: str = Field(max_length=1_000, pattern=r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$")
    as_space: bool
    visibility: Literal["invite_only", "public"]


class UserIDRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: str = Field(
        min_length=3,
        max_length=512,
        pattern=r"^@[^\x00-\x1f\x7f:]+:[^\x00-\x1f\x7f]+$",
    )


class RoomIDRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    room_id: str = Field(
        min_length=3,
        max_length=512,
        pattern=r"^![^\x00-\x1f\x7f:]+:[^\x00-\x1f\x7f]+$",
    )


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def _bounded_json(status_code: int, content: object, *, too_large_message: str) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=content)
    if len(response.body) > _MAX_RESPONSE_BYTES:
        return _error(502, too_large_message)
    return response


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationRejected()
    token = authorization.removeprefix("Bearer ")
    if not token or " " in token or "\t" in token or len(token) > 128:
        raise AuthenticationRejected()
    return token


def _revoke_failed_authority(session_store: BrokerSessionStore, token: str) -> JSONResponse:
    try:
        session_store.logout(token)
    except AuthenticationRejected:
        pass
    return _error(401, "administration session is invalid or expired")


def create_app(
    *,
    session_store: BrokerSessionStore,
    synapse: SynapseAdminAdapter,
) -> FastAPI:
    app = FastAPI(
        title="Hypha administration broker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response
        if request.method in {"POST", "PUT", "PATCH"}:
            content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
            if content_type != "application/json":
                response = _error(415, "invalid request")
            else:
                raw_length = request.headers.get("content-length")
                try:
                    content_length = int(raw_length) if raw_length is not None else None
                except ValueError:
                    content_length = _MAX_REQUEST_BYTES + 1
                if content_length is not None and content_length > _MAX_REQUEST_BYTES:
                    response = _error(413, "request is too large")
                else:
                    body = await request.body()
                    if len(body) > _MAX_REQUEST_BYTES:
                        response = _error(413, "request is too large")
                    else:
                        response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error_value: RequestValidationError) -> JSONResponse:
        return _error(400, "invalid request")

    @app.get(_API_PREFIX + "/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(_API_PREFIX + "/session", status_code=201)
    async def authenticate(request: Request, payload: SessionRequest) -> JSONResponse:
        source = request.client.host if request.client is not None else "unknown"
        try:
            grant = session_store.authenticate(payload.secret, source=source)
        except AuthenticationRejected:
            return _error(401, "administration authentication failed")
        except RateLimited:
            return _error(429, "administration authentication is temporarily unavailable")
        except SessionCapacityExceeded:
            return _error(503, "administration session capacity is unavailable")
        return _bounded_json(
            status_code=201,
            content={
                "session_token": grant.session_token,
                "expires_in_seconds": grant.expires_in_seconds,
                "idle_timeout_seconds": grant.idle_timeout_seconds,
            },
            too_large_message="administration response was too large",
        )

    @app.get(_API_PREFIX + "/snapshot")
    async def snapshot(request: Request) -> JSONResponse:
        try:
            token = _bearer_token(request)
            session_store.authorize(token)
        except AuthenticationRejected:
            return _error(401, "administration session is invalid or expired")
        try:
            upstream = await synapse.snapshot()
            payload = SnapshotPayload.model_validate(upstream)
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, token)
        except (ValidationError, TypeError, ValueError):
            return _error(502, "homeserver administration response was invalid")
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return _bounded_json(
            status_code=200,
            content=payload.model_dump(mode="json"),
            too_large_message="homeserver administration response was too large",
        )

    def authorize_operation(request: Request) -> JSONResponse | None:
        try:
            token = _bearer_token(request)
            session_store.authorize(token)
            request.state.broker_token = token
        except AuthenticationRejected:
            return _error(401, "administration session is invalid or expired")
        return None

    @app.post(_API_PREFIX + "/users", status_code=201)
    async def create_account(request: Request, payload: CreateAccountRequest) -> JSONResponse:
        if denied := authorize_operation(request):
            return denied
        try:
            created = await synapse.create_account(**payload.model_dump())
            validated = UserPayload.model_validate(created)
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, request.state.broker_token)
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return _bounded_json(
            status_code=201,
            content=validated.model_dump(mode="json"),
            too_large_message="homeserver administration response was too large",
        )

    @app.put(_API_PREFIX + "/users/administrator")
    async def set_administrator(request: Request, payload: SetAdministratorRequest) -> JSONResponse:
        if denied := authorize_operation(request):
            return denied
        try:
            updated = await synapse.set_administrator(**payload.model_dump())
            validated = UserPayload.model_validate(updated)
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, request.state.broker_token)
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return _bounded_json(
            status_code=200,
            content=validated.model_dump(mode="json"),
            too_large_message="homeserver administration response was too large",
        )

    @app.get(_API_PREFIX + "/password-reset-requests")
    async def password_reset_requests(request: Request) -> JSONResponse:
        if denied := authorize_operation(request):
            return denied
        try:
            raw_requests = await synapse.password_reset_requests()
            validated = [PasswordResetRequestPayload.model_validate(value) for value in raw_requests]
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, request.state.broker_token)
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return _bounded_json(
            status_code=200,
            content=[value.model_dump(mode="json") for value in validated],
            too_large_message="homeserver administration response was too large",
        )

    @app.put(_API_PREFIX + "/users/password", status_code=204)
    async def reset_password(request: Request, payload: ResetPasswordRequest) -> Response:
        if denied := authorize_operation(request):
            return denied
        try:
            await synapse.reset_password(**payload.model_dump())
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, request.state.broker_token)
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return Response(status_code=204)

    @app.post(_API_PREFIX + "/rooms", status_code=201)
    async def create_room(request: Request, payload: CreateRoomRequest) -> JSONResponse:
        if denied := authorize_operation(request):
            return denied
        try:
            created = await synapse.create_room(**payload.model_dump())
            validated = RoomPayload.model_validate(created)
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, request.state.broker_token)
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return _bounded_json(
            status_code=201,
            content=validated.model_dump(mode="json"),
            too_large_message="homeserver administration response was too large",
        )

    @app.post(_API_PREFIX + "/users/logout", status_code=204)
    async def logout_account(request: Request, payload: UserIDRequest) -> Response:
        if denied := authorize_operation(request):
            return denied
        try:
            await synapse.logout_account(**payload.model_dump())
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, request.state.broker_token)
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return Response(status_code=204)

    @app.post(_API_PREFIX + "/users/deactivate", status_code=204)
    async def deactivate_account(request: Request, payload: UserIDRequest) -> Response:
        if denied := authorize_operation(request):
            return denied
        try:
            await synapse.deactivate_account(**payload.model_dump())
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, request.state.broker_token)
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return Response(status_code=204)

    @app.post(_API_PREFIX + "/rooms/purge", status_code=204)
    async def purge_room(request: Request, payload: RoomIDRequest) -> Response:
        if denied := authorize_operation(request):
            return denied
        try:
            await synapse.purge_room(**payload.model_dump())
        except SynapseAuthorityRejected:
            return _revoke_failed_authority(session_store, request.state.broker_token)
        except Exception:
            return _error(502, "homeserver administration is unavailable")
        return Response(status_code=204)

    @app.delete(_API_PREFIX + "/session", status_code=204)
    async def logout(request: Request) -> Response:
        try:
            token = _bearer_token(request)
            session_store.logout(token)
        except AuthenticationRejected:
            return _error(401, "administration session is invalid or expired")
        return Response(status_code=204)

    return app
