from __future__ import annotations

from typing import Any, Optional

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field


class ToolManifest(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(default="")
    version: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    entrypoint: str = Field(min_length=1)
    env_vars: list[str] = Field(default_factory=list)
    network_access: bool = Field(default=False)
    timeout_ms: int = Field(default=5000, ge=1, le=300_000)
    max_memory_mb: int = Field(default=128, ge=16, le=4096)


class ToolResult(BaseModel):
    request_id: str
    tool_name: str
    success: bool
    exit_code: int
    timed_out: bool = False
    duration_ms: int = 0
    output: Optional[dict[str, Any]] = None
    stdout: str = ""
    stderr: str = ""
    error_message: str = ""


def validate_jsonschema(*, instance: Any, schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    errors = sorted(v.iter_errors(instance), key=lambda e: e.path)
    if errors:
        msg = "; ".join(_format_jsonschema_error(e) for e in errors[:5])
        raise ValueError(f"jsonschema validation failed: {msg}")


def _format_jsonschema_error(err: Any) -> str:
    path = ".".join(str(p) for p in getattr(err, "absolute_path", []))
    if path:
        return f"{path}: {err.message}"
    return str(err.message)
