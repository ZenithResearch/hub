from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FeedConfig:
    id: str                      # unique identifier, e.g. "hn-frontpage"
    name: str                    # human label
    url: str                     # RSS/Atom URL
    poll_interval_s: int = 300   # default 5 minutes
    enqueue_on_match: bool = False   # True = also create a queue job for Frank
    match_keywords: list[str] = field(default_factory=list)  # if set, filter items


@dataclass
class FeedItem:
    feed_id: str
    title: str
    link: str
    published: str
    summary: str
    guid: str  # dedup key
