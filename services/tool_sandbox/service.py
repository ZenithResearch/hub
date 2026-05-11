from __future__ import annotations

import time
from typing import Any

import grpc
from pydantic import ValidationError as PydanticValidationError

from libs.common.errors import (
    ForbiddenAppError,
    NotFoundAppError,
    ValidationAppError,
    grpc_abort,
)
from libs.common.ids import new_request_id
from libs.common.logging import bind_request, clear_request, get_logger
from libs.common.schemas import GrpcRunToolIn, dict_to_struct, struct_to_dict
from libs.tools.contracts import validate_jsonschema
from libs.tools.registry import ToolRegistry
from libs.tools.sandbox_runner import run_tool_subprocess

from libs.common.proto import agent_pb2, agent_pb2_grpc


class ToolSandboxService(agent_pb2_grpc.ToolSandboxServicer):
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        allow_tools_with_network: bool,
        stdout_max_bytes: int,
        stderr_max_bytes: int,
        default_timeout_ms: int,
        default_max_memory_mb: int,
    ) -> None:
        self._registry = registry
        self._allow_tools_with_network = allow_tools_with_network
        self._stdout_max_bytes = stdout_max_bytes
        self._stderr_max_bytes = stderr_max_bytes
        self._default_timeout_ms = default_timeout_ms
        self._default_max_memory_mb = default_max_memory_mb
        self._log = get_logger()

    async def HealthCheck(
        self, request: agent_pb2.HealthCheckRequest, context: grpc.aio.ServicerContext
    ) -> agent_pb2.HealthCheckResponse:
        req_id = request.request_id or new_request_id()
        bind_request(request_id=req_id, grpc_method="ToolSandbox/HealthCheck")
        try:
            return agent_pb2.HealthCheckResponse(
                request_id=req_id,
                status="ok",
                metadata=request.metadata,
            )
        finally:
            clear_request()

    async def RunTool(
        self, request: agent_pb2.RunToolRequest, context: grpc.aio.ServicerContext
    ) -> agent_pb2.RunToolResponse:
        started = time.monotonic()
        req_id = request.request_id or new_request_id()
        bind_request(
            request_id=req_id, grpc_method="ToolSandbox/RunTool", tool_name=request.tool_name
        )
        try:
            try:
                model = GrpcRunToolIn.model_validate(
                    {
                        "request_id": req_id,
                        "tool_name": request.tool_name,
                        "input": struct_to_dict(request.input),
                        "metadata": struct_to_dict(request.metadata),
                    }
                )
            except PydanticValidationError as e:
                raise ValidationAppError(
                    "invalid RunTool request", details={"errors": e.errors()}
                )

            manifest = self._registry.get(model.tool_name)

            if manifest.network_access and not self._allow_tools_with_network:
                raise ForbiddenAppError(
                    "tool requests network_access but ALLOW_TOOLS_WITH_NETWORK is false",
                    details={"tool_name": manifest.name},
                )

            try:
                validate_jsonschema(instance=model.input, schema=manifest.input_schema)
            except Exception as e:
                raise ValidationAppError(
                    "tool input failed jsonschema validation",
                    details={"tool_name": manifest.name, "error": str(e)},
                )

            timeout_ms = int(manifest.timeout_ms or self._default_timeout_ms)
            max_memory_mb = int(manifest.max_memory_mb or self._default_max_memory_mb)

            result = await run_tool_subprocess(
                manifest=manifest,
                tool_input=model.input,
                request_id=req_id,
                timeout_ms=timeout_ms,
                max_memory_mb=max_memory_mb,
                stdout_max_bytes=self._stdout_max_bytes,
                stderr_max_bytes=self._stderr_max_bytes,
            )

            # Validate output contract (if any parsed output exists).
            if result.output is not None:
                try:
                    validate_jsonschema(instance=result.output, schema=manifest.output_schema)
                except Exception as e:
                    result.success = False
                    result.error_message = (
                        result.error_message
                        or f"tool output failed jsonschema validation: {e}"
                    )

            duration_ms = int((time.monotonic() - started) * 1000)
            self._log.info(
                "grpc_run_tool_complete",
                duration_ms=duration_ms,
                success=result.success,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )

            return agent_pb2.RunToolResponse(
                request_id=req_id,
                tool_name=manifest.name,
                success=result.success,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                duration_ms=result.duration_ms,
                output=dict_to_struct(result.output or {}),
                stdout=result.stdout,
                stderr=result.stderr,
                error_message=result.error_message,
                metadata=dict_to_struct({"duration_ms": duration_ms}),
            )
        except (NotFoundAppError, ForbiddenAppError, ValidationAppError) as e:
            await grpc_abort(context, e)
            raise AssertionError("unreachable")  # pragma: no cover
        except grpc.RpcError:
            raise
        except Exception as e:
            self._log.exception("grpc_run_tool_unhandled_error", error=str(e))
            context.abort(grpc.StatusCode.INTERNAL, "internal error")
            raise AssertionError("unreachable")  # pragma: no cover
        finally:
            clear_request()

