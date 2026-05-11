"""
Normalizes Matrix room events into hub queue payloads.

Two trigger shapes (matching chat queue spec):
  - mention: bot was @-tagged in a group room; includes thread context
  - concierge: dedicated 1:1 room; new message only, Frank holds session
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueuePayload:
    """Normalized payload ready to POST to /queues/{name}/enqueue."""
    source_type: str = "chat"
    sender: str = ""
    message_body: str = ""
    event_type: str = "message.received"
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_mention(
    room_id: str,
    sender_matrix_id: str,
    message_body: str,
    thread_events: list[dict],
) -> QueuePayload:
    """Group chat where the bot was @-tagged."""
    participants = list({e.get("sender", "") for e in thread_events if e.get("sender")})
    return QueuePayload(
        source_type="chat",
        sender=sender_matrix_id,
        message_body=message_body,
        event_type="message.received",
        payload={
            "trigger": "mention",
            "channel_id": room_id,
            "session_id": None,
            "message": {
                "participant_id": sender_matrix_id,
                "content": message_body,
            },
            "thread": {
                "participants": participants,
                "messages": thread_events,
            },
        },
    )


def normalize_concierge(
    room_id: str,
    sender_matrix_id: str,
    message_body: str,
    session_id: str | None = None,
) -> QueuePayload:
    """Dedicated 1:1 concierge channel — Frank owns the session."""
    return QueuePayload(
        source_type="chat",
        sender=sender_matrix_id,
        message_body=message_body,
        event_type="message.received",
        payload={
            "trigger": "concierge",
            "channel_id": room_id,
            "session_id": session_id,
            "message": {
                "participant_id": sender_matrix_id,
                "content": message_body,
            },
        },
    )
