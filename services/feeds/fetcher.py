"""RSS/Atom feed fetcher — polls feeds, deduplicates, publishes to event bus."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

import feedparser

from .models import FeedConfig, FeedItem

logger = logging.getLogger(__name__)


class FeedFetcher:
    def __init__(self, db_path: str = "/data/feeds.db") -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_guids (
                    feed_id TEXT NOT NULL,
                    guid    TEXT NOT NULL,
                    seen_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (feed_id, guid)
                )
            """)

    def _is_seen(self, feed_id: str, guid: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_guids WHERE feed_id=? AND guid=?", (feed_id, guid)
            ).fetchone()
            return row is not None

    def _mark_seen(self, feed_id: str, guid: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_guids (feed_id, guid) VALUES (?,?)",
                (feed_id, guid),
            )

    async def fetch_new(self, config: FeedConfig) -> list[FeedItem]:
        """Fetch feed, return only items not seen before."""
        loop = asyncio.get_event_loop()
        parsed = await loop.run_in_executor(None, feedparser.parse, config.url)
        new_items: list[FeedItem] = []
        for entry in parsed.entries:
            guid = entry.get("id") or entry.get("link", "")
            if not guid or self._is_seen(config.id, guid):
                continue
            title = entry.get("title", "")
            # Keyword filter
            if config.match_keywords:
                if not any(kw.lower() in title.lower() for kw in config.match_keywords):
                    self._mark_seen(config.id, guid)
                    continue
            item = FeedItem(
                feed_id=config.id,
                title=title,
                link=entry.get("link", ""),
                published=entry.get("published", ""),
                summary=entry.get("summary", "")[:500],
                guid=guid,
            )
            new_items.append(item)
            self._mark_seen(config.id, guid)
        return new_items
