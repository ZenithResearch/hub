from __future__ import annotations

import time
from typing import Callable

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from libs.common.ids import new_request_id
from libs.common.logging import bind_request, clear_request, get_logger


_REVIEW_ASSET_PATH = "/v1/reviews/assets"
_REVIEW_ASSET_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        limit = (
            _REVIEW_ASSET_MAX_BYTES
            if scope.get("path") == _REVIEW_ASSET_PATH
            else self._max_body_bytes
        )
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                received += len(body)
                if received > limit:
                    raise _BodyTooLarge()
            return message

        try:
            await self._app(scope, limited_receive, send)
        except _BodyTooLarge:
            resp = PlainTextResponse("request body too large", status_code=413)
            await resp(scope, receive, send)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, service: str = "gateway_http") -> None:
        self._app = app
        self._service = service
        self._log = get_logger()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _get_request_id(scope) or new_request_id()
        method = scope.get("method")
        path = scope.get("path")

        scope.setdefault("state", {})["request_id"] = request_id
        bind_request(request_id=request_id, method=method, path=path)

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status") or 0)
                headers = MutableHeaders(scope=message)
                headers.setdefault("x-request-id", request_id)
            await send(message)

        started = time.monotonic()
        status_code: int | None = None
        try:
            await self._app(scope, receive, send_wrapper)
            # Status code is only available via http.response.start; not captured here.
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._log.info(
                "http_request_complete",
                request_id=request_id,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
            )
            clear_request()


def _get_request_id(scope: Scope) -> str | None:
    for k, v in scope.get("headers") or []:
        if k.lower() == b"x-request-id":
            try:
                return v.decode("utf-8")
            except Exception:
                return None
    return None


class _BodyTooLarge(Exception):
    pass

