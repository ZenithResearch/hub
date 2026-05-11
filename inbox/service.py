"""
gRPC QueueService implementation.
"""
from __future__ import annotations

import grpc
from google.protobuf import struct_pb2

from libs.common.proto import queue_pb2, queue_pb2_grpc
from libs.common.ids import new_id
from libs.common.logging import get_logger

from .models import Message
from .store import QueueStore

log = get_logger()


def _struct_to_dict(s: struct_pb2.Struct | None) -> dict:
    if s is None:
        return {}
    return dict(s)


def _message_to_proto(msg: Message) -> queue_pb2.Message:
    payload_struct = struct_pb2.Struct()
    payload_struct.update(msg.payload or {})
    meta_struct = struct_pb2.Struct()
    meta_struct.update(msg.metadata or {})
    return queue_pb2.Message(
        id=msg.id,
        queue_name=msg.queue_name,
        event_type=msg.event_type,
        source_type=msg.source_type,
        sender=msg.sender,
        message_body=msg.message_body,
        payload=payload_struct,
        status=msg.status,
        priority=msg.priority,
        created_at=msg.created_at,
        claimed_at=msg.claimed_at,
        done_at=msg.done_at,
        worker_id=msg.worker_id,
        retry_count=msg.retry_count,
        max_retries=msg.max_retries,
        claim_timeout_s=msg.claim_timeout_s,
        error=msg.error,
        metadata=meta_struct,
    )


class QueueServicer(queue_pb2_grpc.QueueServiceServicer):
    def __init__(self, store: QueueStore, default_max_retries: int = 3, default_claim_timeout_s: int = 300) -> None:
        self._store = store
        self._default_max_retries = default_max_retries
        self._default_claim_timeout_s = default_claim_timeout_s

    def Enqueue(
        self, request: queue_pb2.EnqueueRequest, context: grpc.ServicerContext
    ) -> queue_pb2.EnqueueResponse:
        msg_id = new_id("msg")
        msg = Message(
            id=msg_id,
            queue_name=request.queue_name or "workspace",
            event_type=request.event_type or "service_request",
            source_type=request.source_type,
            sender=request.sender,
            message_body=request.message_body,
            payload=_struct_to_dict(request.payload),
            priority=request.priority,
            max_retries=request.max_retries or self._default_max_retries,
            claim_timeout_s=request.claim_timeout_s or self._default_claim_timeout_s,
            metadata=_struct_to_dict(request.metadata),
        )
        self._store.enqueue(msg)
        log.info("message_enqueued", message_id=msg_id, queue=msg.queue_name, event_type=msg.event_type, source_type=msg.source_type)
        return queue_pb2.EnqueueResponse(request_id=request.request_id, message_id=msg_id)

    def Dequeue(
        self, request: queue_pb2.DequeueRequest, context: grpc.ServicerContext
    ) -> queue_pb2.DequeueResponse:
        msg = self._store.dequeue(request.queue_name, request.worker_id)
        if msg is None:
            return queue_pb2.DequeueResponse(request_id=request.request_id, found=False)
        log.info("message_claimed", message_id=msg.id, queue=request.queue_name, worker=request.worker_id)
        return queue_pb2.DequeueResponse(
            request_id=request.request_id, found=True, message=_message_to_proto(msg)
        )

    def Ack(
        self, request: queue_pb2.AckRequest, context: grpc.ServicerContext
    ) -> queue_pb2.AckResponse:
        ok = self._store.ack(request.message_id, _struct_to_dict(request.result))
        if ok:
            log.info("message_acked", message_id=request.message_id)
        else:
            log.warning("message_ack_failed", message_id=request.message_id)
        return queue_pb2.AckResponse(request_id=request.request_id, ok=ok)

    def Nack(
        self, request: queue_pb2.NackRequest, context: grpc.ServicerContext
    ) -> queue_pb2.NackResponse:
        new_status = self._store.nack(request.message_id, request.reason, request.force_dlq)
        log.info("message_nacked", message_id=request.message_id, new_status=new_status, reason=request.reason)
        return queue_pb2.NackResponse(
            request_id=request.request_id, ok=True, new_status=new_status
        )

    def GetMessage(
        self, request: queue_pb2.GetMessageRequest, context: grpc.ServicerContext
    ) -> queue_pb2.GetMessageResponse:
        msg = self._store.get_message(request.message_id)
        if msg is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"message {request.message_id!r} not found")
        return queue_pb2.GetMessageResponse(request_id=request.request_id, message=_message_to_proto(msg))

    def ListQueues(
        self, request: queue_pb2.ListQueuesRequest, context: grpc.ServicerContext
    ) -> queue_pb2.ListQueuesResponse:
        infos = self._store.list_queues()
        return queue_pb2.ListQueuesResponse(
            request_id=request.request_id,
            queues=[
                queue_pb2.QueueInfo(
                    queue_name=q.queue_name,
                    pending=q.pending,
                    processing=q.processing,
                    done=q.done,
                    failed=q.failed,
                    dlq=q.dlq,
                )
                for q in infos
            ],
        )

    def Peek(
        self, request: queue_pb2.PeekRequest, context: grpc.ServicerContext
    ) -> queue_pb2.PeekResponse:
        messages = self._store.peek(
            request.queue_name,
            n=request.n or 10,
            status=request.status or "pending",
        )
        return queue_pb2.PeekResponse(
            request_id=request.request_id,
            messages=[_message_to_proto(m) for m in messages],
        )

    def HealthCheck(
        self, request: queue_pb2.QueueHealthCheckRequest, context: grpc.ServicerContext
    ) -> queue_pb2.QueueHealthCheckResponse:
        return queue_pb2.QueueHealthCheckResponse(
            request_id=request.request_id,
            status="ok",
            total_queues=self._store.total_queues(),
            total_pending=self._store.total_pending(),
        )
