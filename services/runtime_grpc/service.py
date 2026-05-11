from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from pydantic import ValidationError as PydanticValidationError

from libs.common.errors import (
    ExternalServiceAppError,
    NotFoundAppError,
    ValidationAppError,
    grpc_abort,
)
from libs.common.ids import new_request_id
from libs.common.logging import bind_request, clear_request, get_logger
from libs.common.schemas import (
    GrpcInvokeToolIn,
    GrpcSearchKnowledgeIn,
    GrpcStreamEventsIn,
    GrpcSubmitUserMessageIn,
    dict_to_struct,
    struct_to_dict,
)
from libs.kb.embeddings import DeterministicEmbeddingProvider
from libs.kb.qdrant_store import QdrantVectorStore
from libs.tools.contracts import validate_jsonschema
from libs.tools.registry import ToolRegistry

from libs.common.proto import agent_pb2, agent_pb2_grpc

from .event_buffer import RuntimeEventBuffer


class AgentRuntimeService(agent_pb2_grpc.AgentRuntimeServicer):
    def __init__(
        self,
        *,
        event_buffer: RuntimeEventBuffer,
        tool_registry: ToolRegistry,
        tool_sandbox_target: str,
        grpc_client_timeout_s: float,
        qdrant_url: str,
        qdrant_api_key: str | None,
        qdrant_collection: str,
        kb_vector_dim: int,
        tool_default_timeout_ms: int,
        tool_default_max_memory_mb: int,
    ) -> None:
        self._events = event_buffer
        self._tool_registry = tool_registry
        self._tool_sandbox_target = tool_sandbox_target
        self._grpc_client_timeout_s = grpc_client_timeout_s
        self._tool_default_timeout_ms = tool_default_timeout_ms
        self._tool_default_max_memory_mb = tool_default_max_memory_mb

        emb = DeterministicEmbeddingProvider(vector_dim=kb_vector_dim)
        self._kb_store = QdrantVectorStore(
            url=qdrant_url,
            api_key=qdrant_api_key,
            collection=qdrant_collection,
            embedding_provider=emb,
            vector_dim=kb_vector_dim,
        )

        self._tool_channel: Optional[grpc.aio.Channel] = None
        self._tool_stub: Optional[agent_pb2_grpc.ToolSandboxStub] = None
        self._log = get_logger()

    async def _tool_client(self) -> agent_pb2_grpc.ToolSandboxStub:
        if self._tool_stub and self._tool_channel:
            return self._tool_stub
        self._tool_channel = grpc.aio.insecure_channel(self._tool_sandbox_target)
        self._tool_stub = agent_pb2_grpc.ToolSandboxStub(self._tool_channel)
        return self._tool_stub

    async def close(self) -> None:
        if self._tool_channel:
            await self._tool_channel.close()
            self._tool_channel = None
            self._tool_stub = None

    async def HealthCheck(
        self, request: agent_pb2.HealthCheckRequest, context: grpc.aio.ServicerContext
    ) -> agent_pb2.HealthCheckResponse:
        req_id = request.request_id or new_request_id()
        bind_request(request_id=req_id, grpc_method="AgentRuntime/HealthCheck")
        try:
            return agent_pb2.HealthCheckResponse(
                request_id=req_id,
                status="ok",
                metadata=request.metadata,
            )
        finally:
            clear_request()

    async def SubmitUserMessage(
        self,
        request: agent_pb2.SubmitUserMessageRequest,
        context: grpc.aio.ServicerContext,
    ) -> agent_pb2.SubmitUserMessageResponse:
        started = time.monotonic()
        req_id = request.request_id or new_request_id()
        bind_request(
            request_id=req_id,
            grpc_method="AgentRuntime/SubmitUserMessage",
            user_id=request.user_id,
            session_id=request.session_id,
        )
        try:
            try:
                model = GrpcSubmitUserMessageIn.model_validate(
                    {
                        "request_id": req_id,
                        "user_id": request.user_id,
                        "session_id": request.session_id,
                        "message": request.message,
                        "metadata": struct_to_dict(request.metadata),
                    }
                )
            except PydanticValidationError as e:
                raise ValidationAppError(
                    "invalid SubmitUserMessage request", details={"errors": e.errors()}
                )

            # Placeholder event sequence (no reasoning).
            events = [
                agent_pb2.RuntimeEvent(
                    request_id=req_id,
                    seq=1,
                    type="message_received",
                    payload=dict_to_struct(
                        {
                            "user_id": model.user_id,
                            "session_id": model.session_id,
                        }
                    ),
                    done=False,
                ),
                agent_pb2.RuntimeEvent(
                    request_id=req_id,
                    seq=2,
                    type="runtime_stub",
                    payload=dict_to_struct({"note": "no agent reasoning implemented"}),
                    done=False,
                ),
                agent_pb2.RuntimeEvent(
                    request_id=req_id,
                    seq=3,
                    type="done",
                    payload=dict_to_struct({}),
                    done=True,
                ),
            ]
            await self._events.put(req_id, events)

            duration_ms = int((time.monotonic() - started) * 1000)
            self._log.info("submit_user_message_complete", duration_ms=duration_ms)

            return agent_pb2.SubmitUserMessageResponse(
                request_id=req_id,
                status="accepted",
                runtime_response=dict_to_struct(
                    {
                        "echo": {
                            "user_id": model.user_id,
                            "session_id": model.session_id,
                            "message": model.message,
                            "metadata": model.metadata or {},
                        }
                    }
                ),
            )
        except ValidationAppError as e:
            await grpc_abort(context, e)
            raise AssertionError("unreachable")  # pragma: no cover
        except Exception as e:
            self._log.exception("submit_user_message_unhandled_error", error=str(e))
            context.abort(grpc.StatusCode.INTERNAL, "internal error")
            raise AssertionError("unreachable")  # pragma: no cover
        finally:
            clear_request()

    async def StreamRuntimeEvents(
        self,
        request: agent_pb2.StreamRuntimeEventsRequest,
        context: grpc.aio.ServicerContext,
    ):
        started = time.monotonic()
        req_id = request.request_id
        bind_request(request_id=req_id, grpc_method="AgentRuntime/StreamRuntimeEvents")
        try:
            try:
                _ = GrpcStreamEventsIn.model_validate(
                    {"request_id": req_id, "metadata": struct_to_dict(request.metadata)}
                )
            except PydanticValidationError as e:
                # Streaming RPC: abort early on invalid arguments.
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
                return

            events = await self._events.get(req_id)
            if not events:
                events = [
                    agent_pb2.RuntimeEvent(
                        request_id=req_id,
                        seq=1,
                        type="not_found",
                        payload=dict_to_struct({"request_id": req_id}),
                        done=True,
                    )
                ]

            for ev in events:
                yield ev
                await asyncio.sleep(0.15)

            duration_ms = int((time.monotonic() - started) * 1000)
            self._log.info("stream_runtime_events_complete", duration_ms=duration_ms)
        finally:
            clear_request()

    async def SearchKnowledge(
        self,
        request: agent_pb2.SearchKnowledgeRequest,
        context: grpc.aio.ServicerContext,
    ) -> agent_pb2.SearchKnowledgeResponse:
        started = time.monotonic()
        req_id = request.request_id or new_request_id()
        bind_request(request_id=req_id, grpc_method="AgentRuntime/SearchKnowledge")
        try:
            try:
                model = GrpcSearchKnowledgeIn.model_validate(
                    {
                        "request_id": req_id,
                        "query": request.query,
                        "doc_types": list(request.doc_types),
                        "k": request.k or 5,
                        "metadata": struct_to_dict(request.metadata),
                    }
                )
            except PydanticValidationError as e:
                raise ValidationAppError(
                    "invalid SearchKnowledge request", details={"errors": e.errors()}
                )

            try:
                hits = await asyncio.to_thread(
                    self._kb_store.search,
                    model.query,
                    list(model.doc_types) if model.doc_types else None,
                    int(model.k),
                )
            except Exception as e:
                raise ExternalServiceAppError(
                    "kb search failed", details={"error": str(e)}
                )

            resp_hits = []
            for h in hits:
                resp_hits.append(
                    agent_pb2.SearchHit(
                        score=h.score,
                        document=_doc_to_proto(h.document),
                    )
                )

            duration_ms = int((time.monotonic() - started) * 1000)
            self._log.info(
                "search_knowledge_complete", duration_ms=duration_ms, hit_count=len(resp_hits)
            )

            return agent_pb2.SearchKnowledgeResponse(
                request_id=req_id,
                hits=resp_hits,
                metadata=dict_to_struct({"duration_ms": duration_ms}),
            )
        except (ValidationAppError, ExternalServiceAppError) as e:
            await grpc_abort(context, e)
            raise AssertionError("unreachable")  # pragma: no cover
        except Exception as e:
            self._log.exception("search_knowledge_unhandled_error", error=str(e))
            context.abort(grpc.StatusCode.INTERNAL, "internal error")
            raise AssertionError("unreachable")  # pragma: no cover
        finally:
            clear_request()

    async def InvokeTool(
        self,
        request: agent_pb2.InvokeToolRequest,
        context: grpc.aio.ServicerContext,
    ) -> agent_pb2.InvokeToolResponse:
        started = time.monotonic()
        req_id = request.request_id or new_request_id()
        bind_request(
            request_id=req_id,
            grpc_method="AgentRuntime/InvokeTool",
            tool_name=request.tool_name,
        )
        try:
            try:
                model = GrpcInvokeToolIn.model_validate(
                    {
                        "request_id": req_id,
                        "tool_name": request.tool_name,
                        "input": struct_to_dict(request.input),
                        "metadata": struct_to_dict(request.metadata),
                    }
                )
            except PydanticValidationError as e:
                raise ValidationAppError(
                    "invalid InvokeTool request", details={"errors": e.errors()}
                )

            manifest = self._tool_registry.get(model.tool_name)

            try:
                validate_jsonschema(instance=model.input, schema=manifest.input_schema)
            except Exception as e:
                raise ValidationAppError(
                    "tool input failed jsonschema validation",
                    details={"tool_name": manifest.name, "error": str(e)},
                )

            # gRPC timeout should exceed tool timeout by a small buffer.
            tool_timeout_ms = int(manifest.timeout_ms or self._tool_default_timeout_ms)
            grpc_timeout_s = max(
                float(self._grpc_client_timeout_s), (tool_timeout_ms / 1000.0) + 1.0
            )

            stub = await self._tool_client()
            sandbox_resp = await stub.RunTool(
                agent_pb2.RunToolRequest(
                    request_id=req_id,
                    tool_name=manifest.name,
                    input=dict_to_struct(model.input),
                    metadata=dict_to_struct({"caller": "runtime_grpc"}),
                ),
                timeout=grpc_timeout_s,
            )

            output_dict = struct_to_dict(sandbox_resp.output)
            success = bool(sandbox_resp.success)
            error_message = str(sandbox_resp.error_message or "")

            if output_dict:
                try:
                    validate_jsonschema(instance=output_dict, schema=manifest.output_schema)
                except Exception as e:
                    success = False
                    error_message = error_message or f"tool output failed jsonschema validation: {e}"

            duration_ms = int((time.monotonic() - started) * 1000)
            self._log.info(
                "invoke_tool_complete",
                duration_ms=duration_ms,
                success=success,
                exit_code=int(sandbox_resp.exit_code),
                timed_out=bool(sandbox_resp.timed_out),
            )

            return agent_pb2.InvokeToolResponse(
                request_id=req_id,
                tool_name=manifest.name,
                success=success,
                exit_code=int(sandbox_resp.exit_code),
                timed_out=bool(sandbox_resp.timed_out),
                duration_ms=int(sandbox_resp.duration_ms),
                output=dict_to_struct(output_dict),
                stdout=str(sandbox_resp.stdout),
                stderr=str(sandbox_resp.stderr),
                error_message=error_message,
                metadata=dict_to_struct({"duration_ms": duration_ms}),
            )
        except (NotFoundAppError, ValidationAppError) as e:
            await grpc_abort(context, e)
            raise AssertionError("unreachable")  # pragma: no cover
        except grpc.aio.AioRpcError as e:
            self._log.warning("tool_sandbox_rpc_error", details=str(e))
            await grpc_abort(
                context,
                ExternalServiceAppError(
                    "tool sandbox unavailable", details={"error": str(e)}
                ),
            )
            raise AssertionError("unreachable")  # pragma: no cover
        except Exception as e:
            self._log.exception("invoke_tool_unhandled_error", error=str(e))
            context.abort(grpc.StatusCode.INTERNAL, "internal error")
            raise AssertionError("unreachable")  # pragma: no cover
        finally:
            clear_request()


def _doc_to_proto(doc: Any) -> agent_pb2.Document:
    created = _dt_to_ts(doc.created_at)
    updated = _dt_to_ts(doc.updated_at)
    return agent_pb2.Document(
        doc_id=doc.doc_id,
        doc_type=doc.doc_type,
        title=doc.title,
        content=doc.content,
        tags=list(doc.tags),
        source=doc.source,
        created_at=created,
        updated_at=updated,
    )


def _dt_to_ts(dt: datetime) -> Timestamp:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts

