from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class MessageStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    DLQ = "dlq"


@dataclass
class Message:
    id: str
    queue_name: str
    event_type: str = "service_request"
    process_path: str = ""
    source_type: str = ""
    sender: str = ""
    message_body: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = MessageStatus.PENDING
    priority: int = 0
    created_at: str = ""
    claimed_at: str = ""
    done_at: str = ""
    worker_id: str = ""
    retry_count: int = 0
    max_retries: int = 3
    claim_timeout_s: int = 300
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueueInfo:
    queue_name: str
    pending: int = 0
    processing: int = 0
    done: int = 0
    failed: int = 0
    dlq: int = 0
