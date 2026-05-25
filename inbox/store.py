"""
SQLite-backed message queue store.

Design principles:
- SQLite with WAL mode for concurrent reads + single-writer
- Atomic claim via UPDATE ... WHERE status='pending' + thread lock (SQLite has no SKIP LOCKED)
- Messages move through: pending → processing → done | failed | dlq
- Stale claimed messages (claim_timeout_s exceeded) are re-queued by the reaper
- Table is 'messages'; each row is a service request
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from .models import Message, MessageStatus, QueueInfo

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS messages (
    id                TEXT    PRIMARY KEY,           -- msg_{uuid4}
    queue_name        TEXT    NOT NULL DEFAULT 'workspace',
    event_type        TEXT    NOT NULL DEFAULT 'service_request',
    process_path      TEXT    NOT NULL DEFAULT '',
    source_type       TEXT    NOT NULL DEFAULT '',
    sender            TEXT    NOT NULL DEFAULT '',
    message_body      TEXT    NOT NULL DEFAULT '',
    payload           TEXT    NOT NULL DEFAULT '{}', -- JSON; attachments + producer extras
    status            TEXT    NOT NULL DEFAULT 'pending',
    priority          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    claimed_at        TEXT,
    done_at           TEXT,
    worker_id         TEXT,
    retry_count       INTEGER NOT NULL DEFAULT 0,
    max_retries       INTEGER NOT NULL DEFAULT 3,
    claim_timeout_s   INTEGER NOT NULL DEFAULT 300,
    error             TEXT,
    metadata          TEXT    DEFAULT '{}'           -- JSON
);

CREATE INDEX IF NOT EXISTS idx_messages_queue_status
    ON messages (queue_name, status, priority DESC, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_messages_claimed
    ON messages (status, claimed_at)
    WHERE status = 'processing';
"""


def _now() -> str:
    # SQLite datetime() functions don't accept timezone offsets or microseconds.
    # All timestamps are UTC; use a format SQLite can parse for reaper arithmetic.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class QueueStore:
    """Thread-safe SQLite message queue store."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_db()

    # ──────────────────────────────────────────────
    # Connection management
    # ──────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._conn()
        with self._lock, conn:
            yield conn

    def _init_db(self) -> None:
        with self._tx() as conn:
            conn.executescript(_DDL)
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
            if "process_path" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN process_path TEXT NOT NULL DEFAULT ''")

    # ──────────────────────────────────────────────
    # Write operations
    # ──────────────────────────────────────────────

    def enqueue(self, message: Message) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO messages
                  (id, queue_name, event_type, source_type, sender, message_body,
                   process_path, payload, status, priority, created_at,
                   retry_count, max_retries, claim_timeout_s, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, ?, ?, ?)
                """,
                (
                    message.id,
                    message.queue_name,
                    message.event_type or "service_request",
                    message.source_type or "",
                    message.sender or "",
                    message.message_body or "",
                    message.process_path or "",
                    json.dumps(message.payload),
                    message.priority,
                    message.created_at or _now(),
                    message.max_retries,
                    message.claim_timeout_s,
                    json.dumps(message.metadata) if message.metadata else None,
                ),
            )

    def dequeue(self, queue_name: str, worker_id: str) -> Message | None:
        """
        Atomically claim the highest-priority pending message.
        Uses a lock + UPDATE to avoid concurrent double-claims.
        """
        now = _now()
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT id FROM messages
                WHERE queue_name = ? AND status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                (queue_name,),
            ).fetchone()

            if row is None:
                return None

            conn.execute(
                """
                UPDATE messages SET
                    status     = 'processing',
                    claimed_at = ?,
                    worker_id  = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, worker_id, row["id"]),
            )

        return self.get_message(row["id"])

    def ack(self, message_id: str, result: dict | None = None) -> bool:
        now = _now()
        with self._tx() as conn:
            cur = conn.execute(
                """
                UPDATE messages SET status = 'done', done_at = ?, error = NULL
                WHERE id = ? AND status = 'processing'
                """,
                (now, message_id),
            )
        return cur.rowcount == 1

    def nack(self, message_id: str, reason: str = "", force_dlq: bool = False) -> str:
        """Returns new status: 'pending' (retry) or 'dlq'."""
        now = _now()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT retry_count, max_retries FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return "not_found"

            retry_count = row["retry_count"] + 1
            exhausted = force_dlq or (retry_count > row["max_retries"])
            new_status = MessageStatus.DLQ if exhausted else MessageStatus.PENDING

            conn.execute(
                """
                UPDATE messages SET
                    status      = ?,
                    retry_count = ?,
                    error       = ?,
                    claimed_at  = NULL,
                    worker_id   = NULL,
                    done_at     = CASE WHEN ? = 'dlq' THEN ? ELSE NULL END
                WHERE id = ? AND status = 'processing'
                """,
                (new_status, retry_count, reason, new_status, now, message_id),
            )
        return new_status

    # ──────────────────────────────────────────────
    # Read operations
    # ──────────────────────────────────────────────

    def get_message(self, message_id: str) -> Message | None:
        row = self._conn().execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return _row_to_message(row) if row else None

    def list_queues(self) -> list[QueueInfo]:
        rows = self._conn().execute(
            """
            SELECT
                queue_name,
                COUNT(*) FILTER (WHERE status = 'pending')    AS pending,
                COUNT(*) FILTER (WHERE status = 'processing') AS processing,
                COUNT(*) FILTER (WHERE status = 'done')       AS done,
                COUNT(*) FILTER (WHERE status = 'failed')     AS failed,
                COUNT(*) FILTER (WHERE status = 'dlq')        AS dlq
            FROM messages
            GROUP BY queue_name
            """
        ).fetchall()
        return [
            QueueInfo(
                queue_name=r["queue_name"],
                pending=r["pending"],
                processing=r["processing"],
                done=r["done"],
                failed=r["failed"],
                dlq=r["dlq"],
            )
            for r in rows
        ]

    def peek(
        self,
        queue_name: str,
        n: int = 10,
        status: str = "pending",
        include_payload: bool = True,
    ) -> list[Message]:
        columns = "*" if include_payload else """
                id, queue_name, event_type, process_path, source_type, sender,
                substr(message_body, 1, 240) AS message_body,
                '{}' AS payload, status, priority, created_at, claimed_at,
                done_at, worker_id, retry_count, max_retries, claim_timeout_s,
                error, '{}' AS metadata
            """
        rows = self._conn().execute(
            f"""
            SELECT {columns} FROM messages
            WHERE queue_name = ? AND status = ?
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
            """,
            (queue_name, status, n),
        ).fetchall()
        return [_row_to_message(r) for r in rows]

    def total_pending(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) AS n FROM messages WHERE status = 'pending'"
        ).fetchone()
        return row["n"] if row else 0

    def total_queues(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(DISTINCT queue_name) AS n FROM messages"
        ).fetchone()
        return row["n"] if row else 0

    # ──────────────────────────────────────────────
    # Reaper — reclaim stale processing messages
    # ──────────────────────────────────────────────

    def reap_stale(self) -> int:
        """
        Re-queue messages that have been in 'processing' longer than claim_timeout_s.
        Called periodically by the reaper loop.
        Returns the number of messages re-queued.
        """
        with self._tx() as conn:
            cur = conn.execute(
                """
                UPDATE messages SET
                    status     = 'pending',
                    claimed_at = NULL,
                    worker_id  = NULL,
                    error      = 'claim_timeout'
                WHERE status = 'processing'
                  AND datetime(claimed_at, '+' || claim_timeout_s || ' seconds')
                      <= datetime('now')
                """
            )
        return cur.rowcount


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        queue_name=row["queue_name"],
        event_type=row["event_type"] or "service_request",
        process_path=row["process_path"] or "",
        source_type=row["source_type"] or "",
        sender=row["sender"] or "",
        message_body=row["message_body"] or "",
        payload=json.loads(row["payload"]) if row["payload"] else {},
        status=row["status"],
        priority=row["priority"],
        created_at=row["created_at"] or "",
        claimed_at=row["claimed_at"] or "",
        done_at=row["done_at"] or "",
        worker_id=row["worker_id"] or "",
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        claim_timeout_s=row["claim_timeout_s"],
        error=row["error"] or "",
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )
