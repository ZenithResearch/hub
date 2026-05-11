"""
Event broker — in-memory pub/sub with SSE fan-out.

Topics use dot-notation: "queue.job.enqueued", "feed.rss.item", "frank.session.opened"
Wildcard subscriptions: "queue.job.*", "feed.*", "*"
One wildcard per segment only (* matches exactly one segment).
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncGenerator


@dataclass
class Event:
    topic: str
    source: str
    payload: dict
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_payload(self) -> dict:
        return {
            "id": self.id,
            "topic": self.topic,
            "source": self.source,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    def to_sse(self) -> str:
        data = json.dumps(self.to_payload())
        return f"id: {self.id}\ndata: {data}\n\n"


class EventBroker:
    """Thread-safe in-memory event broker."""

    def __init__(self) -> None:
        # topic_pattern → list of asyncio.Queue
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        async with self._lock:
            targets = [
                q
                for pattern, queues in self._subscribers.items()
                for q in queues
                if self._matches(pattern, event.topic)
            ]
        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer — drop rather than block

    async def subscribe(
        self, topic_pattern: str, max_queue: int = 256
    ) -> AsyncGenerator[Event, None]:
        q: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=max_queue)
        async with self._lock:
            self._subscribers.setdefault(topic_pattern, []).append(q)
        try:
            while True:
                event = await q.get()
                if event is None:
                    break
                yield event
        finally:
            async with self._lock:
                try:
                    self._subscribers[topic_pattern].remove(q)
                    if not self._subscribers[topic_pattern]:
                        del self._subscribers[topic_pattern]
                except (KeyError, ValueError):
                    pass

    async def close_all(self) -> None:
        async with self._lock:
            for queues in self._subscribers.values():
                for q in queues:
                    await q.put(None)

    @staticmethod
    def _matches(pattern: str, topic: str) -> bool:
        return fnmatch.fnmatch(topic, pattern)

    @property
    def subscriber_count(self) -> int:
        return sum(len(qs) for qs in self._subscribers.values())


# Module-level singleton shared across the process
broker = EventBroker()
