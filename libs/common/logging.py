from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars


def configure_logging(*, service: str, level: str = "info") -> None:
    """Configure stdlib logging + structlog JSON output.

    Call once at process start.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=_to_stdlib_level(level),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_to_stdlib_level(level)),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Default bindings applied to all logs.
    bind_contextvars(service=service)


def get_logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger()


def bind_request(*, request_id: str, **extra: Any) -> None:
    bind_contextvars(request_id=request_id, **extra)


def clear_request() -> None:
    clear_contextvars()


def _to_stdlib_level(level: str) -> int:
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get(level.lower(), logging.INFO)

