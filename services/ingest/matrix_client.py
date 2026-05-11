"""
Matrix bot client — connects to Synapse, listens for room events,
normalizes them, and routes to the queue or event bus.
"""
from __future__ import annotations

import json
import logging
import re

import aiohttp
from nio import AsyncClient, MatrixRoom, RoomMessageText

from .config import IngestSettings
from .normalizer import normalize_concierge, normalize_mention

logger = logging.getLogger(__name__)

# Regex to detect @mentions of the bot
_MENTION_RE = re.compile(r"@[\w\-.]+:[\w\-.]+")


class HubBotClient:
    def __init__(self, settings: IngestSettings) -> None:
        self.settings = settings
        self.client = AsyncClient(settings.matrix_homeserver, settings.matrix_user)
        self._http: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._http = aiohttp.ClientSession()
        resp = await self.client.login(self.settings.matrix_password)
        logger.info("Matrix login: %s", resp)
        self.client.add_event_callback(self._on_message, RoomMessageText)
        # Initial sync to consume backlog without processing it, then listen
        await self.client.sync(timeout=0)
        logger.info("Ingest bot connected — listening for room events")
        await self.client.sync_forever(timeout=30_000, full_state=False)

    async def stop(self) -> None:
        await self.client.close()
        if self._http:
            await self._http.close()

    # ── Event handler ─────────────────────────────────────────────────────────

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        sender = event.sender
        body = event.body

        # Ignore messages from ourselves
        if sender == self.settings.matrix_user:
            return

        is_concierge = room.room_id in self.settings.concierge_room_ids
        bot_mentioned = self.settings.matrix_user in body or self._mentioned(body)

        if is_concierge:
            payload = normalize_concierge(
                room_id=room.room_id,
                sender_matrix_id=sender,
                message_body=body,
            )
            await self._enqueue(payload)

        elif bot_mentioned:
            thread = await self._fetch_thread(room.room_id, limit=20)
            payload = normalize_mention(
                room_id=room.room_id,
                sender_matrix_id=sender,
                message_body=body,
                thread_events=thread,
            )
            await self._enqueue(payload)

        else:
            # Non-mention, non-concierge — publish to event bus as broadcast
            await self._publish_event(
                topic="ingest.matrix.message",
                source=f"matrix:{room.room_id}",
                payload={"sender": sender, "body": body, "room_id": room.room_id},
            )

    def _mentioned(self, body: str) -> bool:
        local_part = self.settings.matrix_user.split(":")[0].lstrip("@")
        return local_part.lower() in body.lower()

    async def _fetch_thread(self, room_id: str, limit: int = 20) -> list[dict]:
        """Fetch recent messages for thread context."""
        url = (
            f"{self.settings.matrix_homeserver}/_matrix/client/v3"
            f"/rooms/{room_id}/messages"
        )
        headers = {"Authorization": f"Bearer {self.client.access_token}"}
        try:
            async with self._http.get(
                url, headers=headers, params={"limit": limit, "dir": "b"}
            ) as resp:
                data = await resp.json()
                return [
                    {"sender": e.get("sender"), "content": e.get("content", {}).get("body", "")}
                    for e in data.get("chunk", [])
                    if e.get("type") == "m.room.message"
                ]
        except Exception as exc:
            logger.warning("Thread fetch failed: %s", exc)
            return []

    # ── Routing ───────────────────────────────────────────────────────────────

    async def _enqueue(self, payload) -> None:
        url = f"{self.settings.queue_http_url}/queues/{self.settings.queue_name}/enqueue"
        body = {
            "source_type": payload.source_type,
            "sender": payload.sender,
            "message_body": payload.message_body,
            "event_type": payload.event_type,
            "payload": payload.payload,
            "metadata": payload.metadata,
        }
        try:
            async with self._http.post(url, json=body) as resp:
                data = await resp.json()
                logger.info("Enqueued job %s from %s", data.get("id"), payload.sender)
                # Also publish enqueue event to the event bus
                await self._publish_event(
                    topic="queue.job.enqueued",
                    source="ingest",
                    payload={"job_id": data.get("id"), "sender": payload.sender,
                             "trigger": payload.payload.get("trigger")},
                )
        except Exception as exc:
            logger.error("Enqueue failed: %s", exc)

    async def _publish_event(self, topic: str, source: str, payload: dict) -> None:
        url = f"{self.settings.eventbus_url}/publish"
        try:
            async with self._http.post(
                url, json={"topic": topic, "source": source, "payload": payload}
            ) as resp:
                if resp.status >= 400:
                    logger.warning("Event bus publish failed: %s", await resp.text())
        except Exception as exc:
            logger.warning("Event bus unreachable: %s", exc)
