from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from urllib.parse import quote_plus, urlparse

import grpc
import httpx
import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse

from libs.common.config import GatewaySettings
from libs.common.logging import configure_logging
from libs.common.model_profiles import (
    ModelProfileResolutionError,
    check_model_profile_connectivity,
    load_model_profile_contract,
    resolve_effective_model_profile,
    update_model_profile_binding,
)
from libs.common.proto import agent_pb2, agent_pb2_grpc
from libs.common.schemas import (
    HttpInvokeToolIn,
    HttpMessageIn,
    HttpMessageOut,
    HttpSearchKbIn,
    dict_to_struct,
    struct_to_dict,
)

from .middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from .review_auth import ReviewAuthSession, create_review_auth_store


class ReviewAuthSessionIn(BaseModel):
    project_id: str
    deployment_id: str
    email: str | None = None
    access_code: str
    subject_id: str


class ReviewDeploymentRegisterIn(BaseModel):
    project_id: str
    deployment_slug: str
    branch: str
    allowed_origin: str
    subject_pattern: str
    vercel_deployment_id: str | None = None
    commit_sha: str | None = None


class ReviewDeploymentRegisterOut(BaseModel):
    deployment: dict[str, Any]
    secrets_printed: bool = False


class ReviewAccessPolicyIn(BaseModel):
    deployment_id: str
    deployment_slug: str | None = None
    allowed_origin: str
    subject_pattern: str


class ReviewAccessRotateIn(BaseModel):
    client_id: str
    client_slug: str
    client_name: str
    rolodex_entry_path: str | None = None
    project_id: str
    project_slug: str
    project_name: str
    deployment_id: str | None = None
    deployment_slug: str | None = None
    allowed_origin: str | None = None
    subject_pattern: str | None = None
    policies: list[ReviewAccessPolicyIn] = []
    access_code_id: str
    access_label: str
    access_email: str | None = None
    mode: Literal["generate", "provided"] = "generate"
    access_code: str | None = None
    deployment_scoped_access: bool = False


class ReviewAccessRotateOut(BaseModel):
    client_id: str
    project_id: str
    deployment_id: str | None = None
    access_code_id: str
    access_label: str
    raw_code: str | None = None
    raw_code_present: bool = False
    project_scoped_access: bool
    email_configured: bool
    policy_count: int = 0
    active: bool
    last_rotated_at: str
    secrets_printed: bool = False


class ReviewAccessCapabilitiesOut(BaseModel):
    ok: bool
    hub: str
    capabilities: list[str]
    secrets_printed: bool = False


class ReviewAssetUploadOut(BaseModel):
    asset_id: str
    asset_type: str
    mime_type: str
    size_bytes: int
    created_at: str


class ReviewSubmitIn(BaseModel):
    review_id: str
    subject_id: str
    submitted_by: str | None = None
    started_at: str
    stopped_at: str
    duration_ms: int
    project_id: str
    deployment_id: str
    asset_ids: list[str] = []
    events_asset_id: str | None = None
    audio_asset_id: str | None = None
    metadata: dict[str, Any] = {}


class ReviewSubmitOut(BaseModel):
    review_id: str
    status: str
    created_at: str


class ReviewStatusUpdateIn(BaseModel):
    status: str
    review_note_path: str | None = None
    review_packet_path: str | None = None
    review_packet_status: str | None = None
    reason: str | None = None
    automaton_status: str | None = None
    automaton_event: str | None = None
    review_outcome: str | None = None
    review_scope: str | None = None


class CaseFollowUpIn(BaseModel):
    note: str
    operator: str | None = None
    force_retry: bool = True


class SecretUpdateIn(BaseModel):
    value: str


class ModelProfileBindingUpdateIn(BaseModel):
    updates: dict[str, Any]
    connectivity_result: dict[str, Any] | None = None


class ReviewAccessAdminTokenUpdateOut(BaseModel):
    configured: bool
    capabilities: list[str]
    secrets_printed: bool = False


def _read_secret_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_secret_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{key}={value}\n" for key, value in sorted(values.items()))
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _secret_preview(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _load_image_env_manifest(path: Path | str) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise HTTPException(status_code=503, detail="image/env manifest is not configured")
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("services"), list):
        raise HTTPException(status_code=503, detail="image/env manifest is invalid")
    return data


def _runtime_secret_status(env_name: str, metadata: dict[str, Any]) -> dict[str, Any]:
    value = os.environ.get(env_name, "") or ""
    return {
        **metadata,
        "configured": bool(value),
        "preview": _secret_preview(value) if value else "",
    }


def _safe_image_env_manifest(path: Path | str) -> dict[str, Any]:
    manifest = _load_image_env_manifest(path)
    services: list[dict[str, Any]] = []
    for raw_service in manifest.get("services", []):
        if not isinstance(raw_service, dict):
            continue
        service = dict(raw_service)
        raw_secrets = service.get("secrets")
        secrets_map = raw_secrets if isinstance(raw_secrets, dict) else {}
        service["secrets"] = {
            str(name): _runtime_secret_status(str(name), metadata if isinstance(metadata, dict) else {})
            for name, metadata in sorted(secrets_map.items())
        }
        services.append(service)
    return {
        "schema_version": manifest.get("schema_version"),
        "status": manifest.get("status"),
        "principles": manifest.get("principles", []),
        "services": services,
        "secrets_printed": False,
    }


def _manifest_secret_statuses(path: Path | str, allowed_keys: set[str]) -> dict[str, dict[str, Any]]:
    safe_manifest = _safe_image_env_manifest(path)
    statuses: dict[str, dict[str, Any]] = {}
    for service in safe_manifest["services"]:
        for key, metadata in (service.get("secrets") or {}).items():
            if key in allowed_keys:
                statuses[key] = {**metadata, "service": service.get("service", "")}
    for key in sorted(allowed_keys - set(statuses)):
        statuses[key] = {"configured": bool(os.environ.get(key)), "preview": _secret_preview(os.environ[key]) if os.environ.get(key) else "", "source": "runtime_env", "secret_ref": ""}
    return statuses



_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _safe_session_message(message: Any, index: int) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {"index": index, "role": "unknown", "content_present": False, "content_bytes": 0}
    content = message.get("content")
    if isinstance(content, str):
        content_bytes = len(content.encode("utf-8"))
    elif content is None:
        content_bytes = 0
    else:
        content_bytes = len(json.dumps(content, sort_keys=True).encode("utf-8"))
    return {
        "index": index,
        "role": str(message.get("role") or "unknown")[:32],
        "content_present": content is not None,
        "content_bytes": content_bytes,
    }


def _safe_session_summary(session_payload: dict[str, Any], fallback_session_id: str) -> dict[str, Any]:
    messages = session_payload.get("messages")
    message_list = messages if isinstance(messages, list) else []
    return {
        "session_id": str(session_payload.get("session_id") or fallback_session_id),
        "message_count": len(message_list),
        "messages": [_safe_session_message(message, index) for index, message in enumerate(message_list)],
    }


def _hubfs_error(status_code: int, code: str, path: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "path": path, "detail": detail})


def _decode_hubfs_path(encoded_path: str) -> str:
    try:
        padded = encoded_path + "=" * (-len(encoded_path) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_encoded_path", "detail": "HubFS path is not valid base64url"},
        ) from exc


def _hubfs_roots(settings: GatewaySettings) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in str(settings.hubfs_allowed_roots or "/data").split(os.pathsep):
        value = raw.strip()
        if not value:
            continue
        try:
            resolved = Path(value).expanduser().resolve(strict=False)
        except OSError:
            continue
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            roots.append(resolved)
    return roots


def _hubfs_path(settings: GatewaySettings, raw_path: str) -> tuple[str, Path, Path]:
    value = str(raw_path or "").strip()
    if not value or not value.startswith("/") or "\x00" in value:
        raise _hubfs_error(422, "invalid_path", value, "HubFS path must be absolute")
    try:
        resolved = Path(value).expanduser().resolve(strict=False)
    except OSError as exc:
        raise _hubfs_error(422, "invalid_path", value, f"HubFS path is invalid: {exc}") from None
    roots = _hubfs_roots(settings)
    if not roots:
        raise _hubfs_error(503, "roots_not_configured", str(resolved), "HubFS roots are not configured")
    matches = [root for root in roots if resolved == root or root in resolved.parents]
    if not matches:
        raise _hubfs_error(403, "outside_namespace", str(resolved), "HubFS path is outside configured namespaces")
    namespace = max(matches, key=lambda root: len(str(root)))
    return str(resolved), resolved, namespace


def _hubfs_ref(path: str) -> str:
    return "hubfs_" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]


def _hubfs_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _hubfs_entry(path: Path, namespace: Path) -> dict[str, Any]:
    stat = path.stat()
    kind = "directory" if path.is_dir() else "file"
    normalized = str(path)
    return {
        "path": normalized,
        "name": path.name or normalized,
        "kind": kind,
        "exists": True,
        "size": None if kind == "directory" else stat.st_size,
        "mime_type": None if kind == "directory" else _hubfs_mime_type(path),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "digest": None,
        "readable": True,
        "ref": _hubfs_ref(normalized),
        "namespace": str(namespace),
    }


def _hubfs_existing_path(settings: GatewaySettings, raw_path: str, *, require_file: bool = False, require_directory: bool = False) -> tuple[str, Path, Path]:
    normalized, resolved, namespace = _hubfs_path(settings, raw_path)
    if not resolved.exists():
        raise _hubfs_error(404, "not_found", normalized, "HubFS path not found")
    if require_file and resolved.is_dir():
        raise _hubfs_error(409, "is_directory", normalized, "HubFS path is a directory")
    if require_directory and not resolved.is_dir():
        raise _hubfs_error(409, "not_directory", normalized, "HubFS path is not a directory")
    return normalized, resolved, namespace


def _hubfs_list_entries(path: Path, namespace: Path, *, recursive: bool, limit: int) -> tuple[list[dict[str, Any]], bool]:
    entries: list[dict[str, Any]] = []
    iterator = path.rglob("*") if recursive else path.iterdir()
    truncated = False
    for child in sorted(iterator, key=lambda item: str(item)):
        if len(entries) >= limit:
            truncated = True
            break
        entries.append(_hubfs_entry(child, namespace))
    return entries, truncated


def create_app() -> FastAPI:
    settings = GatewaySettings()
    configure_logging(service="gateway_http", level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        channel = grpc.aio.insecure_channel(settings.runtime_grpc_target)
        app.state.grpc_channel = channel
        app.state.runtime_stub = agent_pb2_grpc.AgentRuntimeStub(channel)
        try:
            yield
        finally:
            await channel.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings

    # Middleware
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_body_bytes)
    app.add_middleware(RequestContextMiddleware)

    allow_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        req_id = getattr(request.state, "request_id", "") or ""
        stub: agent_pb2_grpc.AgentRuntimeStub = request.app.state.runtime_stub
        settings_: GatewaySettings = request.app.state.settings

        try:
            resp = await stub.HealthCheck(
                agent_pb2.HealthCheckRequest(request_id=req_id, metadata=dict_to_struct({})),
                timeout=settings_.grpc_timeout_s,
            )
        except grpc.aio.AioRpcError as e:
            raise HTTPException(status_code=503, detail=f"runtime_grpc error: {e.code().name}")

        return JSONResponse(
            {"status": "ok", "request_id": resp.request_id, "runtime_status": resp.status}
        )

    @app.post("/v1/messages", response_model=HttpMessageOut)
    async def post_message(payload: HttpMessageIn, request: Request):
        req_id = getattr(request.state, "request_id", "") or ""
        stub: agent_pb2_grpc.AgentRuntimeStub = request.app.state.runtime_stub
        settings_: GatewaySettings = request.app.state.settings

        try:
            resp = await stub.SubmitUserMessage(
                agent_pb2.SubmitUserMessageRequest(
                    request_id=req_id,
                    user_id=payload.user_id,
                    session_id=payload.session_id,
                    message=payload.message,
                    metadata=dict_to_struct(payload.metadata or {}),
                ),
                timeout=settings_.grpc_timeout_s,
            )
        except grpc.aio.AioRpcError as e:
            raise HTTPException(status_code=502, detail=f"runtime_grpc error: {e.code().name}")

        return HttpMessageOut(
            request_id=resp.request_id,
            status=resp.status,
            runtime_response=struct_to_dict(resp.runtime_response) if resp.runtime_response else None,
        )

    @app.get("/v1/stream")
    async def stream(request_id: str, request: Request):
        stub: agent_pb2_grpc.AgentRuntimeStub = request.app.state.runtime_stub
        settings_: GatewaySettings = request.app.state.settings

        async def gen() -> AsyncIterator[bytes]:
            try:
                call = stub.StreamRuntimeEvents(
                    agent_pb2.StreamRuntimeEventsRequest(
                        request_id=request_id,
                        metadata=dict_to_struct({}),
                    ),
                    timeout=max(settings_.grpc_timeout_s, 30.0),
                )
                async for ev in call:
                    data = {
                        "request_id": ev.request_id,
                        "seq": ev.seq,
                        "type": ev.type,
                        "payload": struct_to_dict(ev.payload),
                        "done": ev.done,
                    }
                    yield f"data: {json.dumps(data)}\n\n".encode("utf-8")
                    if ev.done:
                        break
                    if await request.is_disconnected():
                        break
            except grpc.aio.AioRpcError as e:
                data = {"type": "error", "payload": {"code": e.code().name}}
                yield f"data: {json.dumps(data)}\n\n".encode("utf-8")

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache",
                "connection": "keep-alive",
                "x-accel-buffering": "no",
            },
        )

    @app.post("/v1/kb/search")
    async def search_kb(payload: HttpSearchKbIn, request: Request) -> JSONResponse:
        req_id = getattr(request.state, "request_id", "") or ""
        stub: agent_pb2_grpc.AgentRuntimeStub = request.app.state.runtime_stub
        settings_: GatewaySettings = request.app.state.settings

        try:
            resp = await stub.SearchKnowledge(
                agent_pb2.SearchKnowledgeRequest(
                    request_id=req_id,
                    query=payload.query,
                    doc_types=list(payload.doc_types or []),
                    k=int(payload.k),
                    metadata=dict_to_struct(payload.metadata or {}),
                ),
                timeout=settings_.grpc_timeout_s,
            )
        except grpc.aio.AioRpcError as e:
            raise HTTPException(status_code=502, detail=f"runtime_grpc error: {e.code().name}")

        hits = []
        for h in resp.hits:
            hits.append(
                {
                    "score": h.score,
                    "document": {
                        "doc_id": h.document.doc_id,
                        "doc_type": h.document.doc_type,
                        "title": h.document.title,
                        "content": h.document.content,
                        "tags": list(h.document.tags),
                        "source": h.document.source,
                        "created_at": h.document.created_at.ToDatetime().isoformat(),
                        "updated_at": h.document.updated_at.ToDatetime().isoformat(),
                    },
                }
            )

        return JSONResponse(
            {
                "request_id": resp.request_id,
                "hits": hits,
                "metadata": struct_to_dict(resp.metadata),
            }
        )

    @app.post("/v1/tools/invoke")
    async def invoke_tool(payload: HttpInvokeToolIn, request: Request) -> JSONResponse:
        req_id = getattr(request.state, "request_id", "") or ""
        stub: agent_pb2_grpc.AgentRuntimeStub = request.app.state.runtime_stub
        settings_: GatewaySettings = request.app.state.settings

        try:
            resp = await stub.InvokeTool(
                agent_pb2.InvokeToolRequest(
                    request_id=req_id,
                    tool_name=payload.tool_name,
                    input=dict_to_struct(payload.input),
                    metadata=dict_to_struct(payload.metadata or {}),
                ),
                timeout=max(settings_.grpc_timeout_s, 30.0),
            )
        except grpc.aio.AioRpcError as e:
            raise HTTPException(status_code=502, detail=f"runtime_grpc error: {e.code().name}")

        return JSONResponse(
            {
                "request_id": resp.request_id,
                "tool_name": resp.tool_name,
                "success": resp.success,
                "exit_code": resp.exit_code,
                "timed_out": resp.timed_out,
                "duration_ms": resp.duration_ms,
                "output": struct_to_dict(resp.output),
                "stdout": resp.stdout,
                "stderr": resp.stderr,
                "error_message": resp.error_message,
                "metadata": struct_to_dict(resp.metadata),
            }
        )

    def _clients_postgres_dsn() -> str:
        explicit = (settings.clients_database_url or "").strip()
        if explicit:
            return explicit
        if not settings.clients_pg_host or not settings.clients_pg_password:
            return ""
        user = quote_plus(settings.clients_pg_user)
        password = quote_plus(settings.clients_pg_password)
        host = settings.clients_pg_host
        port = settings.clients_pg_port
        database = quote_plus(settings.clients_pg_database)
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"

    # Review intake storage
    reviews_dir = Path(settings.reviews_data_dir)
    assets_dir = reviews_dir / "assets"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    review_auth_store = create_review_auth_store(
        backend=settings.clients_db_backend,
        db_path=settings.clients_db_path,
        postgres_dsn=_clients_postgres_dsn(),
        session_ttl_seconds=settings.review_session_ttl_seconds,
    )
    app.state.review_auth_store = review_auth_store
    secret_path = Path(settings.hub_config_secrets_path)
    allowed_secret_keys = {"ELEVENLABS_API_KEY"}

    def _secret_status(key: str, values: dict[str, str]) -> dict[str, Any]:
        # Legacy dynamic file support is retained only for REVIEW_ACCESS_ADMIN_TOKEN below.
        # Runtime provider secrets are reported from the image/env manifest plus process
        # environment injected by ECS/Secrets Manager.
        value = values.get(key) or ""
        return {
            "configured": bool(value),
            "preview": _secret_preview(value) if value else "",
        }

    @app.get("/v1/admin/config/image-env-manifest")
    async def get_admin_image_env_manifest(request: Request) -> JSONResponse:
        _require_review_access_admin(request)
        return JSONResponse(_safe_image_env_manifest(settings.image_env_manifest_path))

    @app.get("/v1/admin/config")
    async def get_admin_config(request: Request) -> JSONResponse:
        _require_review_access_admin(request)
        return JSONResponse(
            {
                "secrets": _manifest_secret_statuses(settings.image_env_manifest_path, allowed_secret_keys),
                "provider_secret_writes": {"supported": False, "backend": "aws_secrets_manager", "targets": []},
                "secrets_printed": False,
            }
        )

    @app.put("/v1/admin/config/secrets/{key}")
    async def put_admin_secret(key: str, payload: SecretUpdateIn, request: Request) -> JSONResponse:
        _require_review_access_admin(request)
        if key not in allowed_secret_keys:
            raise HTTPException(status_code=404, detail="config key is not allowlisted")
        raise HTTPException(
            status_code=410,
            detail="Runtime provider secrets are managed by AWS Secrets Manager; update the secret in the configured backend and redeploy/restart the target service for injection.",
        )

    @app.delete("/v1/admin/config/secrets/{key}")
    async def delete_admin_secret(key: str, request: Request) -> JSONResponse:
        _require_review_access_admin(request)
        if key not in allowed_secret_keys:
            raise HTTPException(status_code=404, detail="config key is not allowlisted")
        raise HTTPException(
            status_code=410,
            detail="Runtime provider secrets are managed by AWS Secrets Manager; remove or rotate the configured secret handle through the deployment control plane.",
        )

    @app.post("/v1/admin/config/validate/stt")
    async def validate_stt_config(request: Request) -> JSONResponse:
        _require_review_access_admin(request)
        configured = bool(os.environ.get("ELEVENLABS_API_KEY"))
        return JSONResponse({"ok": configured, "missing": [] if configured else ["ELEVENLABS_API_KEY"], "secrets_printed": False})

    def _effective_review_access_admin_token() -> str:
        values = _read_secret_file(secret_path)
        dynamic = (values.get("REVIEW_ACCESS_ADMIN_TOKEN") or "").strip()
        if dynamic:
            return dynamic
        return settings.review_access_admin_token.strip()

    def _review_access_admin_capabilities() -> list[str]:
        return ["review_access_admin", "review_access_rotate"]

    def _require_review_access_admin(request: Request) -> None:
        expected = _effective_review_access_admin_token()
        if not expected:
            raise HTTPException(status_code=503, detail="review access admin token is not configured")
        authorization = request.headers.get("authorization") or ""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="invalid review access admin token")
        if not hmac.compare_digest(token.strip(), expected):
            raise HTTPException(status_code=401, detail="invalid review access admin token")

    @app.put("/v1/admin/review-auth/admin-token", response_model=ReviewAccessAdminTokenUpdateOut)
    async def put_review_access_admin_token(payload: SecretUpdateIn, request: Request) -> JSONResponse:
        existing = _effective_review_access_admin_token()
        if existing:
            _require_review_access_admin(request)
        raw_value = payload.value
        if "\n" in raw_value or "\r" in raw_value:
            raise HTTPException(status_code=422, detail="admin token must be single-line")
        value = raw_value.strip()
        if len(value) < 32:
            raise HTTPException(status_code=422, detail="admin token must be at least 32 characters")
        values = _read_secret_file(secret_path)
        values["REVIEW_ACCESS_ADMIN_TOKEN"] = value
        _write_secret_file(secret_path, values)
        return JSONResponse(
            {
                "configured": True,
                "capabilities": _review_access_admin_capabilities(),
                "secrets_printed": False,
            }
        )

    @app.get("/v1/admin/review-auth/capabilities", response_model=ReviewAccessCapabilitiesOut)
    async def get_review_access_admin_capabilities(request: Request) -> JSONResponse:
        _require_review_access_admin(request)
        return JSONResponse(
            {
                "ok": True,
                "hub": "gateway-http",
                "capabilities": _review_access_admin_capabilities(),
                "secrets_printed": False,
            }
        )

    @app.get("/v1/admin/model-profiles/effective")
    async def get_effective_model_profile(
        request: Request,
        agent: str,
        profile: str,
        deployment_profile: str,
    ) -> JSONResponse:
        _require_review_access_admin(request)
        try:
            contract = load_model_profile_contract(Path(settings.model_profiles_path), Path(settings.model_profile_overrides_path))
            effective = resolve_effective_model_profile(
                contract,
                agent=agent,
                profile=profile,
                deployment_profile=deployment_profile,
            )
        except ModelProfileResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return JSONResponse(effective)

    @app.post("/v1/admin/model-profiles/connectivity-check")
    async def post_model_profile_connectivity_check(
        request: Request,
        agent: str,
        profile: str,
        deployment_profile: str,
    ) -> JSONResponse:
        _require_review_access_admin(request)
        try:
            contract = load_model_profile_contract(Path(settings.model_profiles_path), Path(settings.model_profile_overrides_path))
            effective = resolve_effective_model_profile(
                contract,
                agent=agent,
                profile=profile,
                deployment_profile=deployment_profile,
            )
            result = await check_model_profile_connectivity(effective)
        except ModelProfileResolutionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return JSONResponse(result, status_code=200 if result.get("ok") else 502)

    @app.put("/v1/admin/model-profiles/bindings")
    async def put_model_profile_binding(
        payload: ModelProfileBindingUpdateIn,
        request: Request,
        agent: str,
        profile: str,
        deployment_profile: str,
    ) -> JSONResponse:
        _require_review_access_admin(request)
        actor = (request.headers.get("x-zenith-operator") or "gateway-admin").strip()
        try:
            result = update_model_profile_binding(
                contract_path=Path(settings.model_profiles_path),
                overrides_path=Path(settings.model_profile_overrides_path),
                audit_path=Path(settings.model_profile_audit_path),
                agent=agent,
                profile=profile,
                deployment_profile=deployment_profile,
                updates=payload.updates,
                actor=actor,
                connectivity_result=payload.connectivity_result,
            )
        except ModelProfileResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return JSONResponse(result)

    async def _admin_proxy_get(upstream_url: str, request: Request, params: dict[str, str] | None = None) -> JSONResponse:
        _require_review_access_admin(request)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(upstream_url, params=params or None, timeout=10.0)
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="upstream admin service unavailable") from None
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": "upstream admin service returned non-json response"}
        return JSONResponse(payload, status_code=response.status_code)


    async def _admin_proxy_content(upstream_url: str, request: Request, params: dict[str, str] | None = None) -> Response:
        _require_review_access_admin(request)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(upstream_url, params=params or None, timeout=30.0)
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="upstream admin service unavailable") from None
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() in {
                "content-type",
                "content-length",
                "content-disposition",
                "x-zenith-artifact-id",
                "x-zenith-artifact-role",
                "x-hubfs-path",
                "x-hubfs-ref",
            }
        }
        return Response(content=response.content, status_code=response.status_code, headers=headers, media_type=response.headers.get("content-type"))

    @app.get("/v1/admin/queues/workspace/peek")
    async def admin_queue_workspace_peek(request: Request) -> JSONResponse:
        params = {
            key: value
            for key, value in request.query_params.items()
            if key in {"n", "limit", "status", "visibility_timeout", "include_payload"}
        }
        return await _admin_proxy_get(f"{settings.queue_http_url}/queues/workspace/peek", request, params)

    @app.get("/v1/admin/cases")
    async def admin_cases(request: Request) -> JSONResponse:
        params = {
            key: value
            for key, value in request.query_params.items()
            if key in {"status", "limit", "include_heavy"}
        }
        return await _admin_proxy_get(f"{settings.cases_http_url}/cases", request, params)

    @app.get("/v1/admin/cases/{case_id}")
    async def admin_case_detail(case_id: str, request: Request) -> JSONResponse:
        return await _admin_proxy_get(f"{settings.cases_http_url}/cases/{case_id}", request)


    @app.get("/v1/admin/fs/stat")
    async def admin_hubfs_stat(request: Request, path: str) -> JSONResponse:
        _require_review_access_admin(request)
        _normalized, resolved, namespace = _hubfs_existing_path(settings, path)
        return JSONResponse(_hubfs_entry(resolved, namespace))

    @app.get("/v1/admin/fs/content")
    async def admin_hubfs_content(request: Request, path: str) -> FileResponse:
        _require_review_access_admin(request)
        normalized, resolved, _namespace = _hubfs_existing_path(settings, path, require_file=True)
        return FileResponse(
            resolved,
            media_type=_hubfs_mime_type(resolved),
            filename=resolved.name,
            headers={
                "X-HubFS-Path": normalized,
                "X-HubFS-Ref": _hubfs_ref(normalized),
                "Cache-Control": "private, max-age=60",
            },
        )

    @app.get("/v1/admin/fs/list")
    async def admin_hubfs_list(request: Request, path: str, recursive: bool = False, limit: int = 1000) -> JSONResponse:
        _require_review_access_admin(request)
        normalized, resolved, namespace = _hubfs_existing_path(settings, path, require_directory=True)
        bounded_limit = max(1, min(limit, 1000))
        entries, truncated = _hubfs_list_entries(resolved, namespace, recursive=recursive, limit=bounded_limit)
        return JSONResponse(
            {
                "root": normalized,
                "recursive": recursive,
                "truncated": truncated,
                "limit": bounded_limit,
                "entries": entries,
            }
        )

    @app.get("/v1/admin/fs/manifest")
    async def admin_hubfs_manifest(request: Request, path: str, recursive: bool = True, limit: int = 1000) -> JSONResponse:
        _require_review_access_admin(request)
        normalized, resolved, namespace = _hubfs_existing_path(settings, path, require_directory=True)
        if recursive and resolved == namespace:
            raise _hubfs_error(403, "manifest_root_too_broad", normalized, "HubFS manifest must be scoped below a namespace root")
        bounded_limit = max(1, min(limit, 1000))
        entries, truncated = _hubfs_list_entries(resolved, namespace, recursive=recursive, limit=bounded_limit)
        return JSONResponse(
            {
                "root": normalized,
                "recursive": recursive,
                "truncated": truncated,
                "limit": bounded_limit,
                "entries": entries,
            }
        )

    @app.get("/v1/admin/fs/by-path/{encoded_path}/content")
    async def admin_hubfs_by_path_content(encoded_path: str, request: Request) -> FileResponse:
        _require_review_access_admin(request)
        path = _decode_hubfs_path(encoded_path)
        normalized, resolved, _namespace = _hubfs_existing_path(settings, path, require_file=True)
        return FileResponse(
            resolved,
            media_type=_hubfs_mime_type(resolved),
            filename=resolved.name,
            headers={
                "X-HubFS-Path": normalized,
                "X-HubFS-Ref": _hubfs_ref(normalized),
                "Cache-Control": "private, max-age=60",
            },
        )

    @app.get("/v1/admin/mirror/files/{encoded_path}/content")
    async def admin_mirror_file_content(encoded_path: str, request: Request) -> Response:
        return await _admin_proxy_content(f"{settings.cases_http_url}/mirror/files/{encoded_path}/content", request)


    @app.get("/v1/admin/case-runs/{run_id}/artifacts")
    async def admin_case_run_artifacts(run_id: str, request: Request) -> JSONResponse:
        return await _admin_proxy_get(f"{settings.cases_http_url}/case-runs/{run_id}/artifacts", request)

    @app.get("/v1/admin/case-runs/{run_id}/artifacts/{artifact_id}")
    async def admin_case_run_artifact(run_id: str, artifact_id: str, request: Request) -> JSONResponse:
        return await _admin_proxy_get(f"{settings.cases_http_url}/case-runs/{run_id}/artifacts/{artifact_id}", request)

    @app.get("/v1/admin/case-runs/{run_id}/artifacts/{artifact_id}/content")
    async def admin_case_run_artifact_content(run_id: str, artifact_id: str, request: Request) -> Response:
        return await _admin_proxy_content(f"{settings.cases_http_url}/case-runs/{run_id}/artifacts/{artifact_id}/content", request)

    @app.get("/v1/admin/execution-artifacts/{artifact_id}")
    async def admin_execution_artifact(artifact_id: str, request: Request) -> JSONResponse:
        return await _admin_proxy_get(f"{settings.cases_http_url}/execution-artifacts/{artifact_id}", request)

    @app.get("/v1/admin/execution-artifacts/{artifact_id}/content")
    async def admin_execution_artifact_content(artifact_id: str, request: Request) -> Response:
        return await _admin_proxy_content(f"{settings.cases_http_url}/execution-artifacts/{artifact_id}/content", request)

    def _validate_review_policy_metadata(
        *,
        deployment_id: str | None,
        allowed_origin: str | None,
        subject_pattern: str | None,
        context: str = "deployment metadata",
    ) -> None:
        missing = [
            name
            for name, value in {
                "deployment_id": deployment_id,
                "allowed_origin": allowed_origin,
                "subject_pattern": subject_pattern,
            }.items()
            if not (value or "").strip()
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"incomplete {context}: missing {', '.join(missing)}",
            )
        parsed_origin = urlparse(allowed_origin or "")
        parsed_subject = urlparse((subject_pattern or "").split("*", 1)[0])
        if not parsed_origin.scheme or not parsed_origin.netloc or parsed_origin.path not in ("", "/"):
            raise HTTPException(status_code=422, detail="allowed_origin must be an origin")
        hostname = (parsed_origin.hostname or "").lower()
        if hostname in {"localhost", "127.0.0.1", "::1"}:
            if parsed_origin.scheme.lower() not in {"http", "https"}:
                raise HTTPException(status_code=422, detail="local review origins must use http or https")
        elif parsed_origin.scheme.lower() != "https":
            raise HTTPException(status_code=422, detail="review origins must use https outside local development")
        if parsed_subject.scheme.lower() != parsed_origin.scheme.lower() or parsed_subject.netloc.lower() != parsed_origin.netloc.lower():
            raise HTTPException(status_code=422, detail="subject_pattern origin must match allowed_origin")

    def _gallery_policy_tuple(policy: ReviewAccessPolicyIn) -> tuple[str, str, str, str]:
        deployment_id = policy.deployment_id.strip()
        return (
            deployment_id,
            (policy.deployment_slug or deployment_id).strip(),
            policy.allowed_origin.strip(),
            policy.subject_pattern.strip(),
        )

    def _validate_gallery_review_access_policies(payload: ReviewAccessRotateIn) -> None:
        if payload.project_id.strip() != "gallery":
            return
        if payload.deployment_scoped_access:
            raise HTTPException(status_code=422, detail="Gallery review access must be project-scoped with explicit policies")
        legacy_ids = {"gallery-dev", "gallery-prod"}
        submitted_policy_ids = {policy.deployment_id.strip() for policy in payload.policies}
        top_level_id = (payload.deployment_id or "").strip()
        if submitted_policy_ids & legacy_ids or top_level_id in legacy_ids:
            raise HTTPException(
                status_code=422,
                detail="Gallery review access cannot rotate legacy deployment IDs; use gallery-local, gallery-production-apex, and gallery-production-www",
            )
        expected = {
            ("gallery-production-apex", "gallery-production-apex", "https://gal-ler-y.com", "https://gal-ler-y.com*"),
            ("gallery-production-www", "gallery-production-www", "https://www.gal-ler-y.com", "https://www.gal-ler-y.com*"),
            ("gallery-local", "gallery-local", "http://localhost:3000", "http://localhost:3000/*"),
        }
        actual = {_gallery_policy_tuple(policy) for policy in payload.policies}
        if actual != expected:
            raise HTTPException(
                status_code=422,
                detail="Gallery review access requires exactly the canonical gallery-production-apex, gallery-production-www, and gallery-local policies",
            )
        if top_level_id:
            top_level_tuple = (
                top_level_id,
                (payload.deployment_slug or top_level_id).strip(),
                (payload.allowed_origin or "").strip(),
                (payload.subject_pattern or "").strip(),
            )
            if top_level_tuple not in expected:
                raise HTTPException(
                    status_code=422,
                    detail="Gallery compatibility deployment metadata must mirror a canonical Gallery policy",
                )

    def _validate_review_access_rotation(payload: ReviewAccessRotateIn) -> str:
        has_deployment_metadata = any(
            bool((value or "").strip())
            for value in (payload.deployment_id, payload.deployment_slug, payload.allowed_origin, payload.subject_pattern)
        )
        if payload.deployment_scoped_access and not (payload.deployment_id or "").strip():
            raise HTTPException(
                status_code=422,
                detail="deployment_id is required for deployment-scoped access",
            )
        if payload.deployment_scoped_access and payload.policies:
            raise HTTPException(
                status_code=422,
                detail="deployment_scoped_access cannot be combined with policy allowlists",
            )
        if has_deployment_metadata:
            _validate_review_policy_metadata(
                deployment_id=payload.deployment_id,
                allowed_origin=payload.allowed_origin,
                subject_pattern=payload.subject_pattern,
            )
        for index, policy in enumerate(payload.policies):
            _validate_review_policy_metadata(
                deployment_id=policy.deployment_id,
                allowed_origin=policy.allowed_origin,
                subject_pattern=policy.subject_pattern,
                context=f"policy[{index}]",
            )
        _validate_gallery_review_access_policies(payload)
        if payload.mode == "provided":
            code = (payload.access_code or "").strip()
            if len(code) < 16:
                raise HTTPException(status_code=422, detail="access_code must be at least 16 characters")
            return code
        return "zrv_" + secrets.token_urlsafe(32)

    @app.post("/v1/admin/review-auth/access-codes/rotate", response_model=ReviewAccessRotateOut)
    async def rotate_review_access_code(payload: ReviewAccessRotateIn, request: Request) -> JSONResponse:
        _require_review_access_admin(request)
        raw_code = _validate_review_access_rotation(payload)
        result = review_auth_store.rotate_access_code(
            client_id=payload.client_id.strip(),
            client_slug=payload.client_slug.strip(),
            client_name=payload.client_name.strip(),
            rolodex_entry_path=(payload.rolodex_entry_path or "").strip() or None,
            project_id=payload.project_id.strip(),
            project_slug=payload.project_slug.strip(),
            project_name=payload.project_name.strip(),
            deployment_id=(payload.deployment_id or "").strip() or None,
            deployment_slug=(payload.deployment_slug or "").strip() or None,
            allowed_origin=(payload.allowed_origin or "").strip() or None,
            subject_pattern=(payload.subject_pattern or "").strip() or None,
            policies=[
                {
                    "deployment_id": policy.deployment_id.strip(),
                    "deployment_slug": (policy.deployment_slug or policy.deployment_id).strip(),
                    "allowed_origin": policy.allowed_origin.strip(),
                    "subject_pattern": policy.subject_pattern.strip(),
                }
                for policy in payload.policies
            ],
            access_code_id=payload.access_code_id.strip(),
            access_label=payload.access_label.strip(),
            access_code=raw_code,
            access_email=(payload.access_email or "").strip() or None,
            deployment_scoped_access=payload.deployment_scoped_access,
        )
        response = {
            **result,
            "raw_code_present": payload.mode == "generate",
            "secrets_printed": False,
        }
        if payload.mode == "generate":
            response["raw_code"] = raw_code
        return JSONResponse(response)

    def _session_roots() -> list[Path]:
        raw = settings.hermes_session_roots.strip()
        if not raw:
            return []
        return [Path(part.strip()) for part in raw.split(",") if part.strip()]

    def _find_session_export(session_id: str) -> Path | None:
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise HTTPException(status_code=422, detail="invalid session id")
        for root in _session_roots():
            if not root.exists():
                continue
            exact = root / f"session_{session_id}.json"
            if exact.exists():
                return exact
            for candidate in root.rglob(f"session_{session_id}.json"):
                if candidate.is_file():
                    return candidate
            for candidate in root.rglob("session_*.json"):
                if session_id in candidate.stem and candidate.is_file():
                    return candidate
        return None

    @app.post("/v1/review-auth/session")
    async def create_review_auth_session(payload: ReviewAuthSessionIn, request: Request) -> JSONResponse:
        origin = request.headers.get("origin") or ""
        if not origin:
            raise HTTPException(status_code=400, detail="Origin header required")
        result = review_auth_store.create_session(
            project_identifier=payload.project_id,
            deployment_identifier=payload.deployment_id,
            origin=origin,
            subject_id=payload.subject_id,
            access_code=payload.access_code,
            email=payload.email,
        )
        if result is None:
            raise HTTPException(status_code=401, detail="invalid review credentials")
        return JSONResponse(result)

    def _require_deploy_hook(request: Request, project_id: str) -> dict[str, Any]:
        authorization = request.headers.get("authorization") or ""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="invalid deploy hook token")
        hook = review_auth_store.validate_deploy_hook_token(token=token.strip(), project_identifier=project_id)
        if hook is None:
            raise HTTPException(status_code=401, detail="invalid deploy hook token")
        return hook

    def _allowed_deploy_hosts(hook: dict[str, Any]) -> list[str]:
        hosts: list[str] = []
        for item in str(hook.get("allowed_host_suffixes") or "").split(","):
            normalized = item.strip().lower().lstrip(".")
            if normalized:
                hosts.append(normalized)
        return hosts

    def _host_matches_allowed_suffix(hostname: str, suffix: str) -> bool:
        return hostname == suffix or hostname.endswith(f".{suffix}")

    def _origin_parts(origin: str) -> tuple[str, str]:
        parsed = urlparse(origin)
        if not parsed.scheme or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise HTTPException(status_code=422, detail="allowed_origin must be an origin, not a URL with path/query/fragment")
        return parsed.scheme.lower(), parsed.netloc.lower()

    def _subject_pattern_origin(subject_pattern: str) -> tuple[str, str]:
        parsed = urlparse(subject_pattern.split("*", 1)[0])
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=422, detail="subject_pattern must include an absolute URL origin")
        return parsed.scheme.lower(), parsed.netloc.lower()

    def _validate_deploy_origin_policy(allowed_origin: str, subject_pattern: str, hook: dict[str, Any]) -> None:
        scheme, host = _origin_parts(allowed_origin)
        hostname = (urlparse(allowed_origin).hostname or "").lower()
        if hostname in {"localhost", "127.0.0.1", "::1"}:
            if scheme not in {"http", "https"}:
                raise HTTPException(status_code=422, detail="local review origins must use http or https")
        else:
            if scheme != "https":
                raise HTTPException(status_code=422, detail="review deploy origins must use https outside local development")
            allowed_hosts = _allowed_deploy_hosts(hook)
            if not allowed_hosts:
                raise HTTPException(status_code=422, detail="review deploy hook has no allowed host suffixes")
            if not any(_host_matches_allowed_suffix(hostname, item) for item in allowed_hosts):
                raise HTTPException(status_code=422, detail="review deploy origin host is not allowed")
        subject_scheme, subject_host = _subject_pattern_origin(subject_pattern)
        if subject_scheme != scheme or subject_host != host:
            raise HTTPException(status_code=422, detail="subject_pattern origin must match allowed_origin")

    @app.post("/v1/review-auth/deployments/register", response_model=ReviewDeploymentRegisterOut)
    async def register_review_deployment(payload: ReviewDeploymentRegisterIn, request: Request) -> JSONResponse:
        hook = _require_deploy_hook(request, payload.project_id)
        _validate_deploy_origin_policy(payload.allowed_origin, payload.subject_pattern, hook)
        deployment = review_auth_store.register_deployment(
            project_identifier=payload.project_id,
            deployment_slug=payload.deployment_slug,
            branch=payload.branch,
            allowed_origin=payload.allowed_origin,
            subject_pattern=payload.subject_pattern,
            vercel_deployment_id=payload.vercel_deployment_id,
            commit_sha=payload.commit_sha,
        )
        if deployment is None:
            raise HTTPException(status_code=404, detail="review project not found")
        return JSONResponse({"deployment": deployment, "secrets_printed": False})

    def _bearer_token(request: Request) -> str:
        authorization = request.headers.get("authorization") or ""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="review authentication required")
        return token.strip()

    def _require_review_session(
        request: Request,
        *,
        project_id: str | None = None,
        deployment_id: str | None = None,
        subject_id: str | None = None,
    ) -> ReviewAuthSession:
        origin = request.headers.get("origin") or ""
        if not origin:
            raise HTTPException(status_code=400, detail="Origin header required")
        session = review_auth_store.validate_token(
            token=_bearer_token(request),
            origin=origin,
            project_identifier=project_id,
            deployment_identifier=deployment_id,
            subject_id=subject_id,
        )
        if session is None:
            raise HTTPException(status_code=401, detail="invalid review session")
        return session

    @app.get("/v1/review-auth/session")
    async def get_review_auth_session(request: Request) -> JSONResponse:
        session = _require_review_session(request)
        return JSONResponse(
            {
                "authenticated": True,
                "session_id": session.session_id,
                "project_id": session.project_id,
                "deployment_id": session.deployment_id,
                "label": session.label,
                "expires_at": session.expires_at,
            }
        )

    @app.post("/v1/reviews/assets", response_model=ReviewAssetUploadOut)
    async def upload_review_asset(
        request: Request,
        file: UploadFile = File(...),
        asset_type: str = Form(...),
        project_id: str = Form(...),
        deployment_id: str = Form(...),
    ) -> ReviewAssetUploadOut:
        session = _require_review_session(request, project_id=project_id, deployment_id=deployment_id)
        asset_id = str(uuid.uuid4())
        data = await file.read()
        (assets_dir / asset_id).write_bytes(data)
        meta = ReviewAssetUploadOut(
            asset_id=asset_id,
            asset_type=asset_type,
            mime_type=file.content_type or "application/octet-stream",
            size_bytes=len(data),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        meta_record = {
            **meta.model_dump(),
            "client_id": session.client_id,
            "project_id": session.project_id,
            "deployment_id": session.deployment_id,
            "auth_session_id": session.session_id,
            "authenticated": True,
            "origin": session.origin,
            "submitted_by": session.label,
        }
        (assets_dir / f"{asset_id}.meta.json").write_text(json.dumps(meta_record))
        return meta

    @app.post("/v1/reviews", response_model=ReviewSubmitOut)
    async def submit_review(payload: ReviewSubmitIn, request: Request) -> ReviewSubmitOut:
        session = _require_review_session(
            request,
            project_id=payload.project_id,
            deployment_id=payload.deployment_id,
            subject_id=payload.subject_id,
        )
        requested_asset_ids = list(payload.asset_ids)
        for typed_id in (payload.events_asset_id, payload.audio_asset_id):
            if typed_id and typed_id not in requested_asset_ids:
                requested_asset_ids.append(typed_id)
        asset_metas = []
        for aid in requested_asset_ids:
            meta_path = assets_dir / f"{aid}.meta.json"
            if not meta_path.exists():
                raise HTTPException(status_code=422, detail=f"asset {aid} not found")
            meta = json.loads(meta_path.read_text())
            if meta.get("project_id") != session.project_id or meta.get("deployment_id") != session.deployment_id:
                raise HTTPException(status_code=422, detail=f"asset {aid} belongs to a different project/deployment")
            if meta.get("auth_session_id") != session.session_id:
                raise HTTPException(status_code=422, detail=f"asset {aid} belongs to a different review session")
            if meta.get("authenticated") is not True:
                raise HTTPException(status_code=422, detail=f"asset {aid} is not authenticated")
            asset_metas.append(meta)
        meta_by_id = {str(meta.get("asset_id") or "").strip(): meta for meta in asset_metas}
        typed_assets = {str(meta.get("asset_type") or "").strip().lower(): meta for meta in asset_metas}
        events_asset_id = payload.events_asset_id or typed_assets.get("events", {}).get("asset_id")
        audio_asset_id = payload.audio_asset_id or typed_assets.get("audio", {}).get("asset_id")
        if not events_asset_id:
            raise HTTPException(status_code=422, detail="review submission requires events_asset_id")
        expected_typed_assets = {"events_asset_id": (events_asset_id, "events")}
        if audio_asset_id:
            expected_typed_assets["audio_asset_id"] = (audio_asset_id, "audio")
        for field, (asset_id, expected_type) in expected_typed_assets.items():
            actual_type = str((meta_by_id.get(str(asset_id)) or {}).get("asset_type") or "").strip().lower()
            if actual_type != expected_type:
                raise HTTPException(status_code=422, detail=f"{field} must reference a {expected_type} asset")
        created_at = datetime.now(timezone.utc).isoformat()
        record = {
            **payload.model_dump(),
            "client_id": session.client_id,
            "project_id": session.project_id,
            "deployment_id": session.deployment_id,
            "auth_session_id": session.session_id,
            "authenticated": True,
            "submitted_by": session.label or payload.submitted_by,
            "origin": session.origin,
            "asset_ids": requested_asset_ids,
            "events_asset_id": events_asset_id,
            "audio_asset_id": audio_asset_id,
            "assets": asset_metas,
            "status": "queued",
            "created_at": created_at,
        }
        (reviews_dir / f"{payload.review_id}.json").write_text(json.dumps(record))

        # Enqueue directly — Frank now handles all queue work through the normal full loop.
        # Convert upstream queue failures into HTTPException responses instead of letting
        # raw httpx exceptions escape as 500s. Raw exceptions bypass browser-visible CORS
        # headers in production, so Safari reports an opaque CORS failure instead of the
        # actual queue-side problem.
        try:
            async with httpx.AsyncClient(timeout=10.0) as q:
                enqueue_resp = await q.post(
                    f"{settings.queue_http_url}/queues/workspace/enqueue",
                    json={
                        "event_type": "review_submitted",
                        "source_type": "review_sdk",
                        "sender": record["submitted_by"],
                        "message_body": payload.review_id,
                        "payload": record,
                    },
                )
                enqueue_resp.raise_for_status()
                try:
                    msg_id = enqueue_resp.json().get("id")
                except ValueError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail="review saved but queue enqueue returned an invalid response",
                    ) from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"review saved but queue enqueue failed: HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail="review saved but queue enqueue failed",
            ) from exc

        # Publish wakeup event to eventbus so Frank picks it up immediately
        async with httpx.AsyncClient(timeout=5.0) as eb:
            try:
                await eb.post(
                    f"{settings.eventbus_url}/publish",
                    json={"topic": "queue.job.enqueued", "source": "gateway_http", "payload": {"job_id": msg_id}},
                )
            except Exception:
                pass

        # Fire-and-forget Matrix notification (optional, no impact on queue path)
        if settings.matrix_homeserver_url and settings.matrix_feedback_room_id:
            try:
                async with httpx.AsyncClient(timeout=5.0) as mx:
                    await mx.post(
                        f"{settings.matrix_homeserver_url}/_matrix/client/v3/rooms"
                        f"/{settings.matrix_feedback_room_id}/send/m.room.message",
                        params={"user_id": settings.matrix_bot_user_id},
                        headers={"Authorization": f"Bearer {settings.matrix_bot_access_token}"},
                        json={"msgtype": "m.text", "body": f"review_submitted: {payload.review_id}"},
                    )
            except Exception:
                pass

        return ReviewSubmitOut(
            review_id=payload.review_id, status="queued", created_at=created_at
        )

    @app.get("/v1/reviews/assets/{asset_id}")
    async def get_review_asset(asset_id: str):
        asset_path = assets_dir / asset_id
        meta_path = assets_dir / f"{asset_id}.meta.json"
        if not asset_path.exists() or not meta_path.exists():
            raise HTTPException(status_code=404, detail="asset not found")
        meta = json.loads(meta_path.read_text())
        from starlette.responses import Response
        ext_map = {
            "application/json": "json",
            "audio/webm": "webm",
            "image/webp": "webp",
        }
        mime = meta.get("mime_type", "application/octet-stream")
        ext = ext_map.get(mime, "bin")
        filename = f"{meta.get('asset_type', 'asset')}.{ext}"
        return Response(
            content=asset_path.read_bytes(),
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.patch("/v1/reviews/{review_id}/status")
    async def update_review_status(review_id: str, payload: ReviewStatusUpdateIn) -> JSONResponse:
        allowed = {"queued", "processing", "processed", "failed"}
        status = payload.status.strip().lower()
        if status not in allowed:
            raise HTTPException(status_code=422, detail="invalid review status")
        path = reviews_dir / f"{review_id}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="review not found")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["status"] = status
        record["status_updated_at"] = datetime.now(timezone.utc).isoformat()
        if payload.review_note_path is not None:
            record["review_note_path"] = payload.review_note_path
        if payload.review_packet_path is not None:
            record["review_packet_path"] = payload.review_packet_path
        if payload.review_packet_status is not None:
            record["review_packet_status"] = payload.review_packet_status
        if payload.reason is not None:
            record["status_reason"] = payload.reason
        for field_name in (
            "automaton_status",
            "automaton_event",
            "review_outcome",
            "review_scope",
        ):
            value = getattr(payload, field_name)
            if value is not None:
                record[field_name] = value
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(record), encoding="utf-8")
        tmp_path.replace(path)
        return JSONResponse({"review_id": review_id, "status": status})

    @app.get("/v1/reviews/{review_id}")
    async def get_review(review_id: str) -> JSONResponse:
        path = reviews_dir / f"{review_id}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="review not found")
        return JSONResponse(json.loads(path.read_text()))

    @app.get("/v1/hermes/sessions/{session_id}")
    async def get_hermes_session(session_id: str) -> JSONResponse:
        path = _find_session_export(session_id)
        if path is None:
            raise HTTPException(status_code=404, detail="session not found")
        session_payload = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse(_safe_session_summary(session_payload, session_id))

    @app.get("/v1/hermes/sessions/{session_id}/messages")
    async def get_hermes_session_messages(session_id: str) -> JSONResponse:
        path = _find_session_export(session_id)
        if path is None:
            raise HTTPException(status_code=404, detail="session not found")
        session_payload = json.loads(path.read_text(encoding="utf-8"))
        summary = _safe_session_summary(session_payload, session_id)
        return JSONResponse({"session_id": summary["session_id"], "messages": summary["messages"]})

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)

    @app.get("/dashboard")
    async def dashboard() -> FileResponse:
        return FileResponse(static_dir / "dashboard.html")

    processes_dir = Path("base/ops/processes")

    @app.get("/v1/processes/{process_name}")
    async def get_process_spec(process_name: str) -> JSONResponse:
        path = processes_dir / f"{process_name}.md"
        if not path.exists():
            raise HTTPException(status_code=404, detail="process spec not found")
        return JSONResponse({"name": process_name, "content": path.read_text()})

    @app.put("/_matrix/app/v1/transactions/{txn_id}")
    async def matrix_appservice_noop(txn_id: str) -> JSONResponse:
        # gateway-bot only sends; this endpoint satisfies Synapse's push requirement
        return JSONResponse({})

    # iss-p15-001 scope baseline: readiness endpoint to be added under admin for Matrix homeserver reachability (redacted)
    # iss-p15-001 contract guard: endpoint must return redacted status; no tokens; distinguish liveness vs readiness; fail-closed on missing config
    # iss-p15-001 primary impl placeholder: add GET /v1/admin/matrix/readiness returning redacted dict
    # iss-p15-001 edge: negative cases - missing config -> degraded, no token leak

    @app.post("/v1/cases/{case_id}/rerun")
    async def rerun_case(case_id: str, force: bool = False) -> JSONResponse:
        """Re-enqueue the original message for a completed, failed, or force-retried case."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            return JSONResponse(await _reenqueue_case(client, case_id=case_id, force=force))

    @app.post("/v1/cases/{case_id}/follow-up")
    async def follow_up_case(case_id: str, payload: CaseFollowUpIn) -> JSONResponse:
        """Attach operator input to a blocked case and optionally force-retry it."""
        note = payload.note.strip()
        if not note:
            raise HTTPException(status_code=422, detail="follow-up note must not be empty")
        async with httpx.AsyncClient(timeout=5.0) as client:
            case_resp = await client.get(f"{settings.cases_http_url}/cases/{case_id}")
            if case_resp.status_code == 404:
                raise HTTPException(status_code=404, detail="case not found")
            case_resp.raise_for_status()
            case_data = case_resp.json()
            case = case_data.get("case", case_data)
            status = str(case.get("status") or "")
            if status != "BLOCKED":
                raise HTTPException(status_code=400, detail=f"follow-up is only available for BLOCKED cases, got {status}")

            submitted_at = datetime.now(timezone.utc).isoformat()
            follow_up = {
                "case_id": case_id,
                "note": note,
                "operator": payload.operator or "ZenithOS",
                "submitted_at": submitted_at,
            }
            log_resp = await client.post(
                f"{settings.cases_http_url}/cases/{case_id}/logs",
                json={
                    "type": "operator_follow_up",
                    "message": f"Operator follow-up submitted: {note}",
                    "metadata": follow_up,
                },
            )
            log_resp.raise_for_status()

            result: dict[str, Any] = {"logged": True, "case_id": case_id, "follow_up": follow_up}
            if payload.force_retry:
                result.update(await _reenqueue_case(client, case_id=case_id, force=True, follow_up=follow_up, case=case))
            return JSONResponse(result)

    async def _reenqueue_case(
        client: httpx.AsyncClient,
        *,
        case_id: str,
        force: bool,
        follow_up: dict[str, Any] | None = None,
        case: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if case is None:
            case_resp = await client.get(f"{settings.cases_http_url}/cases/{case_id}")
            if case_resp.status_code == 404:
                raise HTTPException(status_code=404, detail="case not found")
            case_resp.raise_for_status()
            case_data = case_resp.json()
            case = case_data.get("case", case_data)

        status = case.get("status", "")
        if not force and status not in ("COMPLETE", "COMPLETED", "FAILED"):
            raise HTTPException(status_code=400, detail=f"cannot rerun case with status {status}")

        orig_msg_id = case.get("queue_message_id")
        if not orig_msg_id:
            raise HTTPException(status_code=400, detail="case has no original queue message")

        msg_resp = await client.get(f"{settings.queue_http_url}/messages/{orig_msg_id}")
        if msg_resp.status_code == 404:
            raise HTTPException(status_code=404, detail="original queue message not found")
        msg_resp.raise_for_status()
        orig = msg_resp.json()

        queue_payload = dict(orig.get("payload", {}) or {})
        if follow_up is not None:
            queue_payload.setdefault("operator_follow_ups", []).append(follow_up)
            queue_payload["latest_operator_follow_up"] = follow_up

        enq_resp = await client.post(
            f"{settings.queue_http_url}/queues/workspace/enqueue",
            json={
                "event_type": orig.get("event_type"),
                "source_type": orig.get("source_type"),
                "sender": orig.get("sender"),
                "message_body": orig.get("message_body"),
                "payload": queue_payload,
            },
        )
        enq_resp.raise_for_status()
        new_msg = enq_resp.json()

        try:
            await client.post(
                f"{settings.eventbus_url}/publish",
                json={"topic": "queue.job.enqueued", "source": "gateway_http/follow_up" if follow_up else "gateway_http/rerun",
                      "payload": {"case_id": case_id, "new_message_id": new_msg.get("id")}},
                timeout=3.0,
            )
        except Exception:
            pass

        return {"queued": True, "new_message_id": new_msg.get("id")}

    return app


app = create_app()
