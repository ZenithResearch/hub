from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import boto3
import grpc

from libs.common.config import AgentAdminSettings
from libs.common.logging import configure_logging, get_logger
from libs.common.proto import agent_admin_pb2, agent_admin_pb2_grpc

from .service import AgentAdminService
from .ssm import SsmLifecycleDispatcher
from .store import AgentAdminStore


async def serve() -> None:
    settings = AgentAdminSettings()
    configure_logging(service="agent_admin", level=settings.log_level)
    log = get_logger()

    store = AgentAdminStore(Path(settings.db_path), configured_profile_id=settings.profile_id)
    dispatcher = SsmLifecycleDispatcher(
        client=boto3.client("ssm"),
        instance_id=settings.instance_id,
        document_name=settings.ssm_document_name,
    )
    service = AgentAdminService(
        store=store,
        dispatcher=dispatcher,
        configured_profile_id=settings.profile_id,
        allowed_matrix_secret_arns=settings.matrix_secret_arn_allowlist(),
    )

    server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", 1024 * 1024),
            ("grpc.max_receive_message_length", 1024 * 1024),
        ]
    )
    agent_admin_pb2_grpc.add_AgentAdminServicer_to_server(service, server)
    if server.add_insecure_port(settings.bind_addr) == 0:
        store.close()
        raise RuntimeError("failed to bind Agent Admin gRPC service")
    _enable_reflection(server)

    stop_event = asyncio.Event()

    def _handle_signal(signame: str) -> None:
        log.info("shutdown_signal_received", signal=signame)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda sig=sig: _handle_signal(sig.name))
        except NotImplementedError:  # pragma: no cover
            pass

    log.info("grpc_server_starting", bind_addr=settings.bind_addr)
    await server.start()
    log.info("grpc_server_started", bind_addr=settings.bind_addr)
    await stop_event.wait()
    log.info("grpc_server_stopping")
    await server.stop(grace=5)
    store.close()
    log.info("grpc_server_stopped")


def _enable_reflection(server: grpc.aio.Server) -> None:
    try:
        from grpc_reflection.v1alpha import reflection

        reflection.enable_server_reflection(
            (
                agent_admin_pb2.DESCRIPTOR.services_by_name["AgentAdmin"].full_name,
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
