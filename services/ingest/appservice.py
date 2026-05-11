"""
Matrix Application Service webhook receiver.

Synapse pushes transactions here via:
  PUT /_matrix/app/v1/transactions/{txnId}

Authentication: Synapse sets Authorization: Bearer <hs_token>.
We validate that token on every request before processing.

Replaces the polling-based HubBotClient (matrix_client.py).
"""
from __future__ import annotations

import logging

import aiohttp
from fastapi import FastAPI, Header, HTTPException, Path
from pydantic import BaseModel

from .config import IngestSettings
from .normalizer import normalize_concierge, normalize_mention

logger = logging.getLogger(__name__)

# ── Pydantic models ────────────────────────────────────────────────────────────


class MatrixEventContent(BaseModel):
    body: str | None = None
    msgtype: str | None = None


class MatrixEvent(BaseModel):
    type: str
    room_id: str | None = None
    sender: str | None = None
    event_id: str | None = None
    content: dict = {}


class Transaction(BaseModel):
    events: list[MatrixEvent] = []


# ── App factory ────────────────────────────────────────────────────────────────


def create_app(settings: IngestSettings) -> FastAPI:
    app = FastAPI(title="Hub Ingest — Matrix App Service")
    router = _AppServiceRouter(settings)

    @app.put("/_matrix/app/v1/transactions/{txn_id}", status_code=200)
    async def receive_transaction(
        txn_id: str = Path(...),
        authorization: str | None = Header(default=None),
        body: Transaction = ...,
    ) -> dict:
        _check_hs_token(authorization, settings.hs_token)
        await router.handle_transaction(txn_id, body)
        return {}

    # Synapse may query whether a user in our namespace exists.
    # Returning 200 {} means "yes, this user is managed by us."
    @app.get("/_matrix/app/v1/users/{user_id}", status_code=200)
    async def query_user(
        user_id: str = Path(...),
        authorization: str | None = Header(default=None),
    ) -> dict:
        _check_hs_token(authorization, settings.hs_token)
        return {}

    @app.get("/_matrix/app/v1/rooms/{room_alias}", status_code=404)
    async def query_room(room_alias: str = Path(...)) -> dict:
        # We don't own any room aliases
        raise HTTPException(status_code=404, detail="Room alias not managed by this AS")

    return app


# ── Auth helper ────────────────────────────────────────────────────────────────


def _check_hs_token(authorization: str | None, expected: str) -> None:
    if not expected:
        return  # no token configured — skip check (dev fallback)
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid hs_token")


# ── Router ─────────────────────────────────────────────────────────────────────


class _AppServiceRouter:
    def __init__(self, settings: IngestSettings) -> None:
        self.settings = settings
        self._http: aiohttp.ClientSession | None = None

    def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def handle_transaction(self, txn_id: str, txn: Transaction) -> None:
        for event in txn.events:
            try:
                await self._dispatch(event)
            except Exception as exc:
                logger.error("Error processing event %s: %s", event.event_id, exc)

    async def _dispatch(self, event: MatrixEvent) -> None:
        if event.type != "m.room.message":
            return
        if event.room_id is None or event.sender is None:
            return

        body = event.content.get("body", "")
        if not isinstance(body, str):
            return

        sender = event.sender
        room_id = event.room_id
        bot_user = self.settings.matrix_user

        # Ignore our own messages
        if sender == bot_user:
            return

        is_concierge = room_id in self.settings.concierge_room_ids
        bot_mentioned = bot_user in body or self._mentioned(body)

        if is_concierge:
            payload = normalize_concierge(
                room_id=room_id,
                sender_matrix_id=sender,
                message_body=body,
            )
            await self._enqueue(payload)

        elif bot_mentioned:
            thread = await self._fetch_thread(room_id)
            payload = normalize_mention(
                room_id=room_id,
                sender_matrix_id=sender,
                message_body=body,
                thread_events=thread,
            )
            await self._enqueue(payload)

        else:
            await self._publish_event(
                topic="ingest.matrix.message",
                source=f"matrix:{room_id}",
                payload={"sender": sender, "body": body, "room_id": room_id},
            )

    def _mentioned(self, body: str) -> bool:
        local_part = self.settings.matrix_user.split(":")[0].lstrip("@")
        return local_part.lower() in body.lower()

    async def _fetch_thread(self, room_id: str, limit: int = 20) -> list[dict]:
        encoded = room_id.replace(":", "%3A").replace("!", "%21")
        url = (
            f"{self.settings.matrix_homeserver}/_matrix/client/v3"
            f"/rooms/{encoded}/messages"
        )
        headers = {"Authorization": f"Bearer {self.settings.as_token}"}
        params = {"limit": limit, "dir": "b", "user_id": self.settings.matrix_user}
        try:
            async with self._session().get(url, headers=headers, params=params) as resp:
                data = await resp.json()
                return [
                    {
                        "sender": e.get("sender"),
                        "content": e.get("content", {}).get("body", ""),
                    }
                    for e in data.get("chunk", [])
                    if e.get("type") == "m.room.message"
                ]
        except Exception as exc:
            logger.warning("Thread fetch failed: %s", exc)
            return []

    async def _enqueue(self, payload) -> None:
        url = (
            f"{self.settings.queue_http_url}/queues"
            f"/{self.settings.queue_name}/enqueue"
        )
        body = {
            "source_type": payload.source_type,
            "sender": payload.sender,
            "message_body": payload.message_body,
            "event_type": payload.event_type,
            "payload": payload.payload,
            "metadata": payload.metadata,
        }
        try:
            async with self._session().post(url, json=body) as resp:
                data = await resp.json()
                logger.info(
                    "Enqueued job %s from %s", data.get("id"), payload.sender
                )
                await self._publish_event(
                    topic="queue.job.enqueued",
                    source="ingest",
                    payload={
                        "job_id": data.get("id"),
                        "sender": payload.sender,
                        "trigger": payload.payload.get("trigger"),
                    },
                )
        except Exception as exc:
            logger.error("Enqueue failed: %s", exc)

    async def _publish_event(self, topic: str, source: str, payload: dict) -> None:
        url = f"{self.settings.eventbus_url}/publish"
        try:
            async with self._session().post(
                url, json={"topic": topic, "source": source, "payload": payload}
            ) as resp:
                if resp.status >= 400:
                    logger.warning("Event bus publish failed: %s", await resp.text())
        except Exception as exc:
            logger.warning("Event bus unreachable: %s", exc)
