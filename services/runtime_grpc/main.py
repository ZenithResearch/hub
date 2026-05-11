from __future__ import annotations

import asyncio
import signal

import grpc

from libs.common.config import RuntimeSettings
from libs.common.logging import configure_logging, get_logger
from libs.common.proto import agent_pb2, agent_pb2_grpc
from libs.tools.registry import ToolRegistry

from .event_buffer import RuntimeEventBuffer
from .service import AgentRuntimeService


async def serve() -> None:
    settings = RuntimeSettings()
    configure_logging(service="runtime_grpc", level=settings.log_level)
    log = get_logger()

    tool_registry = ToolRegistry(tool_dir=settings.tool_dir)
    tool_registry.load()
    log.info(
        "tool_registry_loaded",
        tool_count=len(tool_registry.list_tools()),
        tool_dir=settings.tool_dir,
    )

    event_buffer = RuntimeEventBuffer()
    svc = AgentRuntimeService(
        event_buffer=event_buffer,
        tool_registry=tool_registry,
        tool_sandbox_target=settings.tool_sandbox_grpc_target,
        grpc_client_timeout_s=settings.grpc_client_timeout_s,
        qdrant_url=settings.qdrant_url,
        qdrant_api_key=settings.qdrant_api_key,
        qdrant_collection=settings.qdrant_collection,
        kb_vector_dim=settings.kb_vector_dim,
        tool_default_timeout_ms=settings.tool_default_timeout_ms,
        tool_default_max_memory_mb=settings.tool_default_max_memory_mb,
    )

    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", 4 * 1024 * 1024),
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),
        ]
    )
    agent_pb2_grpc.add_AgentRuntimeServicer_to_server(svc, server)
    server.add_insecure_port(settings.bind_addr)

    _enable_reflection(server)

    stop_event: asyncio.Event = asyncio.Event()

    def _handle_signal(signame: str) -> None:
        log.info("shutdown_signal_received", signal=signame)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, lambda s=s: _handle_signal(s.name))
        except NotImplementedError:  # pragma: no cover
            pass

    log.info("grpc_server_starting", bind_addr=settings.bind_addr)
    await server.start()
    log.info("grpc_server_started", bind_addr=settings.bind_addr)

    await stop_event.wait()

    log.info("grpc_server_stopping")
    await server.stop(grace=5)
    await svc.close()
    log.info("grpc_server_stopped")


def _enable_reflection(server: grpc.aio.Server) -> None:
    try:
        from grpc_reflection.v1alpha import reflection  # type: ignore

        reflection.enable_server_reflection(
            (
                agent_pb2.DESCRIPTOR.services_by_name["AgentRuntime"].full_name,
                reflection.SERVICE_NAME,
            ),
            server,
        )
    except Exception:
        return


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

