from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from libs.common.proto import agent_pb2


@dataclass
class BufferedEvents:
    created_at_monotonic: float
    events: list[agent_pb2.RuntimeEvent]


class RuntimeEventBuffer:
    def __init__(self, *, ttl_s: float = 300.0) -> None:
        self._ttl_s = ttl_s
        self._by_request_id: dict[str, BufferedEvents] = {}
        self._lock = asyncio.Lock()

    async def put(self, request_id: str, events: list[agent_pb2.RuntimeEvent]) -> None:
        async with self._lock:
            self._by_request_id[request_id] = BufferedEvents(
                created_at_monotonic=time.monotonic(),
                events=events,
            )
            self._prune_locked()

    async def get(self, request_id: str) -> list[agent_pb2.RuntimeEvent] | None:
        async with self._lock:
            self._prune_locked()
            buffered = self._by_request_id.get(request_id)
            return list(buffered.events) if buffered else None

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [
            rid
            for rid, buf in self._by_request_id.items()
            if (now - buf.created_at_monotonic) > self._ttl_s
        ]
        for rid in expired:
            self._by_request_id.pop(rid, None)

