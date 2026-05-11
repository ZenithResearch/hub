"""
Hub inbox — queue service entry point.

Starts two servers concurrently:
  - gRPC on QUEUE_GRPC_BIND (default 0.0.0.0:50053)
  - HTTP  on QUEUE_HTTP_PORT (default 8081)

A background reaper task periodically reclaims stale claimed messages.
"""
from __future__ import annotations

import asyncio
import signal
import threading
import time

import grpc
import uvicorn
from grpc_reflection.v1alpha import reflection

from libs.common.logging import configure_logging, get_logger
from libs.common.proto import queue_pb2, queue_pb2_grpc

from .config import QueueSettings
from .http import create_app
from .service import QueueServicer
from .store import QueueStore


async def serve() -> None:
    settings = QueueSettings()
    configure_logging(service="queue", level=settings.log_level)
    log = get_logger()

    store = QueueStore(db_path=settings.db_path)
    log.info("queue_store_opened", db_path=settings.db_path)

    # ── gRPC server ──
    servicer = QueueServicer(
        store=store,
        default_max_retries=settings.default_max_retries,
        default_claim_timeout_s=settings.default_claim_timeout_s,
    )
    grpc_server = grpc.aio.server(
        options=[
            ("grpc.max_send_message_length", 4 * 1024 * 1024),
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),
        ]
    )
    queue_pb2_grpc.add_QueueServiceServicer_to_server(servicer, grpc_server)
    _enable_reflection(grpc_server)
    grpc_server.add_insecure_port(settings.grpc_bind)

    # ── HTTP server ──
    http_app = create_app(
        store=store,
        default_max_retries=settings.default_max_retries,
        default_claim_timeout_s=settings.default_claim_timeout_s,
    )
    http_config = uvicorn.Config(
        http_app,
        host="0.0.0.0",
        port=settings.http_port,
        log_config=None,  # handled by structlog
    )
    http_server = uvicorn.Server(http_config)

    # ── Reaper ──
    stop_event = asyncio.Event()

    def _reaper_loop() -> None:
        while not stop_event.is_set():
            time.sleep(settings.reaper_interval_s)
            if stop_event.is_set():
                break
            n = store.reap_stale()
            if n:
                log.info("reaper_reclaimed_messages", count=n)

    reaper_thread = threading.Thread(target=_reaper_loop, daemon=True)

    # ── Signals ──
    def _handle_signal(signame: str) -> None:
        log.info("shutdown_signal_received", signal=signame)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, lambda s=s: _handle_signal(s.name))
        except NotImplementedError:
            pass

    # ── Start ──
    log.info("grpc_starting", bind=settings.grpc_bind)
    await grpc_server.start()
    log.info("http_starting", port=settings.http_port)
    reaper_thread.start()

    http_task = asyncio.create_task(http_server.serve())

    log.info("queue_service_ready", grpc=settings.grpc_bind, http=settings.http_port)
    await stop_event.wait()

    # ── Shutdown ──
    log.info("queue_service_stopping")
    http_server.should_exit = True
    await grpc_server.stop(grace=5)
    await http_task
    log.info("queue_service_stopped")


def _enable_reflection(server: grpc.aio.Server) -> None:
    try:
        reflection.enable_server_reflection(
            (
                queue_pb2.DESCRIPTOR.services_by_name["QueueService"].full_name,
                reflection.SERVICE_NAME,
            ),
            server,
        )
    except Exception:
        pass


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
