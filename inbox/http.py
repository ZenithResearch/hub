"""
FastAPI HTTP interface for the message queue service.

Thin layer over QueueStore — same operations as gRPC, JSON body.
Accessible at QUEUE_HTTP_PORT (default 8081).
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from libs.common.ids import new_id
from libs.common.logging import get_logger

from .models import Message
from .store import QueueStore

log = get_logger()


# ──────────────────────────────────────────────
# Request / response models
# ──────────────────────────────────────────────

class EnqueueBody(BaseModel):
    event_type: str = "service_request"
    process_path: str = ""
    source_type: str = ""
    sender: str = ""
    message_body: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    max_retries: int = 3
    claim_timeout_s: int = 300
    metadata: dict[str, Any] = Field(default_factory=dict)


class EnqueueOut(BaseModel):
    message_id: str
    id: str


class DequeueOut(BaseModel):
    found: bool
    message: dict[str, Any] | None = None


class AckBody(BaseModel):
    result: dict[str, Any] = Field(default_factory=dict)


class NackBody(BaseModel):
    reason: str = ""
    force_dlq: bool = False


class StatusOut(BaseModel):
    ok: bool
    new_status: str | None = None


# ──────────────────────────────────────────────
# App factory
# ──────────────────────────────────────────────

def create_app(store: QueueStore, default_max_retries: int = 3, default_claim_timeout_s: int = 300) -> FastAPI:
    app = FastAPI(title="Hub Message Queue", version="0.2.0", docs_url="/docs")

    # ── Queue operations ──

    @app.post("/queues/{queue_name}/enqueue", response_model=EnqueueOut)
    def enqueue(queue_name: str, body: EnqueueBody) -> EnqueueOut:
        msg_id = new_id("msg")
        msg = Message(
            id=msg_id,
            queue_name=queue_name,
            event_type=body.event_type or "service_request",
            process_path=body.process_path or "",
            source_type=body.source_type,
            sender=body.sender,
            message_body=body.message_body,
            payload=body.payload,
            priority=body.priority,
            max_retries=body.max_retries or default_max_retries,
            claim_timeout_s=body.claim_timeout_s or default_claim_timeout_s,
            metadata=body.metadata,
        )
        store.enqueue(msg)
        log.info("http_message_enqueued", message_id=msg_id, queue=queue_name, event_type=body.event_type, source_type=body.source_type)
        return EnqueueOut(message_id=msg_id, id=msg_id)

    @app.post("/queues/{queue_name}/dequeue", response_model=DequeueOut)
    def dequeue(queue_name: str, worker_id: str = "http") -> DequeueOut:
        msg = store.dequeue(queue_name, worker_id)
        if msg is None:
            return DequeueOut(found=False)
        return DequeueOut(found=True, message=_message_to_dict(msg))

    @app.get("/queues/{queue_name}/peek")
    def peek(queue_name: str, n: int = 10, status: str = "pending") -> dict:
        messages = store.peek(queue_name, n=n, status=status)
        return {"messages": [_message_to_dict(m) for m in messages]}

    @app.get("/queues")
    def list_queues() -> dict:
        return {"queues": [
            {
                "queue_name": q.queue_name,
                "pending": q.pending,
                "processing": q.processing,
                "done": q.done,
                "failed": q.failed,
                "dlq": q.dlq,
            }
            for q in store.list_queues()
        ]}

    # ── Message operations ──

    @app.get("/messages/{message_id}")
    def get_message(message_id: str) -> dict:
        msg = store.get_message(message_id)
        if msg is None:
            raise HTTPException(status_code=404, detail=f"message {message_id!r} not found")
        return _message_to_dict(msg)

    @app.post("/messages/{message_id}/ack", response_model=StatusOut)
    def ack(message_id: str, body: AckBody) -> StatusOut:
        ok = store.ack(message_id, body.result)
        if not ok:
            raise HTTPException(status_code=409, detail="message not in processing state")
        return StatusOut(ok=True)

    @app.post("/messages/{message_id}/nack", response_model=StatusOut)
    def nack(message_id: str, body: NackBody) -> StatusOut:
        new_status = store.nack(message_id, body.reason, body.force_dlq)
        if new_status == "not_found":
            raise HTTPException(status_code=404, detail=f"message {message_id!r} not found")
        return StatusOut(ok=True, new_status=new_status)

    # ── Health ──

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "total_queues": store.total_queues(),
            "total_pending": store.total_pending(),
        }

    # ── Hub identity ──

    @app.get("/identity")
    def identity() -> dict:
        """Public hub identity — who owns this hub and what Matrix server it runs."""
        return {
            "hub_owner_matrix_id": os.getenv("HUB_OWNER_MATRIX_ID", ""),
            "matrix_server_name":  os.getenv("MATRIX_SERVER_NAME", "localhost"),
        }

    return app


def _message_to_dict(msg: Message) -> dict:
    return {
        "id": msg.id,
        "queue_name": msg.queue_name,
        "event_type": msg.event_type,
        "process_path": msg.process_path,
        "source_type": msg.source_type,
        "sender": msg.sender,
        "message_body": msg.message_body,
        "payload": msg.payload,
        "status": msg.status,
        "priority": msg.priority,
        "created_at": msg.created_at,
        "claimed_at": msg.claimed_at,
        "done_at": msg.done_at,
        "worker_id": msg.worker_id,
        "retry_count": msg.retry_count,
        "max_retries": msg.max_retries,
        "claim_timeout_s": msg.claim_timeout_s,
        "error": msg.error,
        "metadata": msg.metadata,
    }
