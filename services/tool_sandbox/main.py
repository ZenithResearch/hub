from __future__ import annotations

import asyncio
import signal

import grpc

from libs.common.config import ToolSandboxSettings
from libs.common.logging import configure_logging, get_logger
from libs.common.proto import agent_pb2, agent_pb2_grpc
from libs.tools.registry import ToolRegistry

from .service import ToolSandboxService


async def serve() -> None:
    settings = ToolSandboxSettings()
    configure_logging(service="tool_sandbox", level=settings.log_level)
    log = get_logger()

    registry = ToolRegistry(tool_dir=settings.tool_dir)
    registry.load()
    log.info(
        "tool_registry_loaded",
        tool_count=len(registry.list_tools()),
        tool_dir=settings.tool_dir,
    )

    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", 4 * 1024 * 1024),
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),
        ]
    )
    agent_pb2_grpc.add_ToolSandboxServicer_to_server(
        ToolSandboxService(
            registry=registry,
            allow_tools_with_network=settings.allow_tools_with_network,
            stdout_max_bytes=settings.stdout_max_bytes,
            stderr_max_bytes=settings.stderr_max_bytes,
            default_timeout_ms=settings.tool_default_timeout_ms,
            default_max_memory_mb=settings.tool_default_max_memory_mb,
        ),
        server,
    )

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
    log.info("grpc_server_stopped")


def _enable_reflection(server: grpc.aio.Server) -> None:
    try:
        from grpc_reflection.v1alpha import reflection  # type: ignore

        reflection.enable_server_reflection(
            (
                agent_pb2.DESCRIPTOR.services_by_name["ToolSandbox"].full_name,
                reflection.SERVICE_NAME,
            ),
            server,
        )
    except Exception:
        # Reflection is optional (used for grpcurl DX).
        return


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

