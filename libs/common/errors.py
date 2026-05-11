from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import grpc


@dataclass
class AppError(Exception):
    code: str
    message: str
    details: Optional[dict[str, Any]] = None

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


class ValidationAppError(AppError):
    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__("validation_error", message, details)


class NotFoundAppError(AppError):
    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__("not_found", message, details)


class ForbiddenAppError(AppError):
    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__("forbidden", message, details)


class ExternalServiceAppError(AppError):
    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__("external_service_error", message, details)


class ToolExecutionAppError(AppError):
    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__("tool_execution_error", message, details)


def http_status_from_error(err: AppError) -> int:
    return {
        "validation_error": 400,
        "not_found": 404,
        "forbidden": 403,
        "external_service_error": 503,
        "tool_execution_error": 409,
    }.get(err.code, 500)


def grpc_status_from_error(err: AppError) -> grpc.StatusCode:
    return {
        "validation_error": grpc.StatusCode.INVALID_ARGUMENT,
        "not_found": grpc.StatusCode.NOT_FOUND,
        "forbidden": grpc.StatusCode.PERMISSION_DENIED,
        "external_service_error": grpc.StatusCode.UNAVAILABLE,
        "tool_execution_error": grpc.StatusCode.FAILED_PRECONDITION,
    }.get(err.code, grpc.StatusCode.UNKNOWN)


async def grpc_abort(context: grpc.aio.ServicerContext, err: AppError) -> None:
    await context.abort(grpc_status_from_error(err), err.message)

