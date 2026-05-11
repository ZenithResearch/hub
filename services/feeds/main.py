"""
Feed service — polls registered feeds, publishes new items to the event bus,
optionally enqueues items for Frank processing.

Feed registry is configured via FEEDS_CONFIG env var (JSON array) or
defaults to an empty list (no feeds polled).

Example FEEDS_CONFIG:
[
  {"id":"hn","name":"Hacker News","url":"https://news.ycombinator.com/rss","poll_interval_s":300},
  {"id":"mysite","name":"My Blog","url":"https://example.com/feed.xml","enqueue_on_match":true,"match_keywords":["AI","agent"]}
]
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import aiohttp

from .fetcher import FeedFetcher
from .models import FeedConfig

logger = logging.getLogger(__name__)

EVENTBUS_URL = os.getenv("EVENTBUS_URL", "http://eventbus:8082")
QUEUE_HTTP_URL = os.getenv("QUEUE_HTTP_URL", "http://queue:8081")
QUEUE_NAME = os.getenv("QUEUE_NAME", "workspace")
FEEDS_CONFIG_RAW = os.getenv("FEEDS_CONFIG", "[]")


def load_feeds() -> list[FeedConfig]:
    try:
        raw = json.loads(FEEDS_CONFIG_RAW)
        return [FeedConfig(**f) for f in raw]
    except Exception as exc:
        logger.warning("Could not parse FEEDS_CONFIG: %s", exc)
        return []


async def poll_feed(
    config: FeedConfig,
    fetcher: FeedFetcher,
    session: aiohttp.ClientSession,
) -> None:
    while True:
        try:
            items = await fetcher.fetch_new(config)
            for item in items:
                logger.info("New feed item [%s]: %s", config.id, item.title)
                # Always publish to event bus
                await session.post(
                    f"{EVENTBUS_URL}/publish",
                    json={
                        "topic": f"feed.{config.id}.item",
                        "source": f"feed:{config.id}",
                        "payload": {
                            "feed_id": item.feed_id,
                            "title": item.title,
                            "link": item.link,
                            "published": item.published,
                            "summary": item.summary,
                        },
                    },
                )
                # Optionally enqueue for Frank
                if config.enqueue_on_match:
                    await session.post(
                        f"{QUEUE_HTTP_URL}/queues/{QUEUE_NAME}/enqueue",
                        json={
                            "source_type": "feed",
                            "sender": config.id,
                            "message_body": item.title,
                            "event_type": "feed.item.received",
                            "payload": {
                                "feed_id": item.feed_id,
                                "link": item.link,
                                "summary": item.summary,
                            },
                        },
                    )
        except Exception as exc:
            logger.error("Feed poll error [%s]: %s", config.id, exc)
        await asyncio.sleep(config.poll_interval_s)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    feeds = load_feeds()
    if not feeds:
        logger.info("No feeds configured — feed service idle. Set FEEDS_CONFIG to add feeds.")
    else:
        logger.info("Starting feed service with %d feed(s)", len(feeds))
    fetcher = FeedFetcher()
    async with aiohttp.ClientSession() as session:
        tasks = [asyncio.create_task(poll_feed(f, fetcher, session)) for f in feeds]
        # Keep running even with no feeds (so it can be reconfigured)
        await asyncio.gather(*tasks) if tasks else await asyncio.Event().wait()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
