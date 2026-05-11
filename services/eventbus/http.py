from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .broker import Event, broker


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        await broker.close_all()


app = FastAPI(title="Hub Event Bus", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Publish ───────────────────────────────────────────────────────────────────

class PublishRequest(BaseModel):
    topic: str
    source: str
    payload: dict = {}


@app.post("/publish", status_code=202)
async def publish(body: PublishRequest) -> dict:
    event = Event(topic=body.topic, source=body.source, payload=body.payload)
    await broker.publish(event)
    return {"id": event.id, "topic": event.topic}


# ── Subscribe (SSE) ───────────────────────────────────────────────────────────

@app.get("/subscribe")
async def subscribe(
    request: Request,
    topic: str = Query(default="*", description="Topic pattern, e.g. 'queue.job.*'"),
):
    """Server-Sent Events stream. Connect and receive matching events in real time."""
    async def event_stream():
        yield {"event": "connected", "data": json.dumps({"topic": topic})}
        try:
            async for event in broker.subscribe(topic):
                if await request.is_disconnected():
                    break
                yield {
                    "id": event.id,
                    "event": event.topic,
                    "data": json.dumps(event.to_payload()),
                }
        except asyncio.CancelledError:
            return

    return EventSourceResponse(event_stream())


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "subscribers": broker.subscriber_count}
