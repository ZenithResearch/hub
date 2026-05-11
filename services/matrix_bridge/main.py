"""
Matrix Bridge — Application Service

Implements the Matrix Application Service HTTP API. Synapse pushes room
transaction events here; the bridge validates the hs_token and relays
structured payloads to the queue and eventbus.

Required environment variables:
  BRIDGE_BOT_HS_TOKEN        Synapse sends this to authenticate transactions
  MATRIX_FEEDBACK_ROOM_ID    Only events from this room are relayed
  QUEUE_HTTP_URL             http://queue:8081
  EVENTBUS_URL               http://eventbus:8082
  MATRIX_BRIDGE_HTTP_PORT    default 8084
"""

from __future__ import annotations

import json
import logging
import os

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from libs.common.config import MatrixBridgeSettings
from libs.common.logging import configure_logging

settings = MatrixBridgeSettings()
configure_logging(service="matrix_bridge", level=settings.log_level)
log = logging.getLogger("matrix_bridge")

app = FastAPI(title="matrix-bridge")

HTTP_PORT = int(os.environ.get("MATRIX_BRIDGE_HTTP_PORT", "8084"))


async def relay_to_queue(body: str) -> None:
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        log.warning("Ignored non-JSON message body")
        return

    event_type = msg.get("event_type", "")
    if not event_type:
        log.warning("Ignored message with no event_type")
        return

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{settings.queue_http_url}/queues/workspace/enqueue",
                json=msg,
                timeout=5.0,
            )
            resp.raise_for_status()
            log.info("Enqueued  event_type=%s", event_type)
        except Exception as exc:
            log.error("Failed to enqueue: %s", exc)
            return

        try:
            await client.post(
                f"{settings.eventbus_url}/publish",
                json={
                    "topic": "queue.job.enqueued",
                    "source": "matrix_bridge",
                    "payload": {"event_type": event_type, "queue": "workspace"},
                },
                timeout=5.0,
            )
        except Exception as exc:
            log.warning("Eventbus publish failed (non-fatal): %s", exc)


@app.put("/_matrix/app/v1/transactions/{txn_id}")
async def handle_transaction(
    txn_id: str = Path(...),
    authorization: str = Header(default=""),
    request: Request = None,
) -> JSONResponse:
    # Validate hs_token — Synapse sends it as "Bearer <hs_token>"
    expected = f"Bearer {settings.matrix_bot_access_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid hs_token")

    body = await request.json()
    for event in body.get("events", []):
        if event.get("type") != "m.room.message":
            continue
        if event.get("room_id") != settings.matrix_feedback_room_id:
            continue
        content = event.get("content", {})
        if content.get("msgtype") != "m.text":
            continue
        log.info("Transaction %s — relaying event from %s", txn_id, event.get("sender"))
        await relay_to_queue(content.get("body", ""))

    return JSONResponse({})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT)
