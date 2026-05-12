from __future__ import annotations

import fnmatch
import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_CODE_HASH_PREFIX = "pbkdf2_sha256"
_CODE_HASH_ITERATIONS = 210_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hash_access_code(code: str, *, salt: bytes | None = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        code.encode("utf-8"),
        salt,
        _CODE_HASH_ITERATIONS,
    )
    return f"{_CODE_HASH_PREFIX}${_CODE_HASH_ITERATIONS}${salt.hex()}:{digest.hex()}"


def verify_access_code(code: str, stored_hash: str) -> bool:
    try:
        prefix, iterations, payload = stored_hash.split("$", 2)
        salt_hex, digest_hex = payload.split(":", 1)
    except ValueError:
        return False
    if prefix != _CODE_HASH_PREFIX:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        code.encode("utf-8"),
        salt,
        int(iterations),
    ).hex()
    return hmac.compare_digest(expected, digest_hex)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def subject_allowed(subject_id: str, origin: str, subject_pattern: str | None) -> bool:
    parsed = urlparse(subject_id)
    subject_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    if subject_origin != origin:
        return False
    pattern = (subject_pattern or "").strip()
    if not pattern:
        return True
    if pattern.startswith("re:"):
        import re

        return re.fullmatch(pattern[3:], subject_id) is not None
    return fnmatch.fnmatch(subject_id, pattern)


@dataclass(frozen=True)
class ReviewAuthSession:
    session_id: str
    client_id: str
    project_id: str
    deployment_id: str
    access_code_id: str
    label: str
    origin: str
    expires_at: str
    deployment_slug: str
    project_slug: str
    subject_pattern: str | None


class ReviewAuthStore:
    def __init__(self, db_path: str, session_ttl_seconds: int) -> None:
        self.db_path = Path(db_path)
        self.session_ttl_seconds = int(session_ttl_seconds)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY,
                    slug TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL REFERENCES clients(id),
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(client_id, slug)
                );
                CREATE TABLE IF NOT EXISTS review_deployments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    slug TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    allowed_origin TEXT NOT NULL,
                    subject_pattern TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, slug)
                );
                CREATE TABLE IF NOT EXISTS review_access_codes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    deployment_id TEXT REFERENCES review_deployments(id),
                    label TEXT NOT NULL,
                    email TEXT,
                    code_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE TABLE IF NOT EXISTS review_sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    deployment_id TEXT NOT NULL REFERENCES review_deployments(id),
                    access_code_id TEXT NOT NULL REFERENCES review_access_codes(id),
                    token_hash TEXT NOT NULL UNIQUE,
                    subject_label TEXT,
                    origin TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_review_sessions_token_hash ON review_sessions(token_hash);
                CREATE INDEX IF NOT EXISTS idx_review_access_codes_project_deployment ON review_access_codes(project_id, deployment_id);
                """
            )

    def _get_project(self, conn: sqlite3.Connection, project_identifier: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT p.*, c.id AS client_id, c.slug AS client_slug
            FROM projects p
            JOIN clients c ON c.id = p.client_id
            WHERE p.id = ? OR p.slug = ?
            ORDER BY CASE WHEN p.id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (project_identifier, project_identifier, project_identifier),
        ).fetchone()

    def _get_deployment(self, conn: sqlite3.Connection, project_id: str, deployment_identifier: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM review_deployments
            WHERE project_id = ? AND (id = ? OR slug = ?) AND active = 1
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (project_id, deployment_identifier, deployment_identifier, deployment_identifier),
        ).fetchone()

    def create_session(
        self,
        *,
        project_identifier: str,
        deployment_identifier: str,
        origin: str,
        subject_id: str,
        access_code: str,
        email: str | None,
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as conn:
            project = self._get_project(conn, project_identifier)
            if project is None:
                return None
            deployment = self._get_deployment(conn, str(project["id"]), deployment_identifier)
            if deployment is None or str(deployment["allowed_origin"]) != origin:
                return None
            if not subject_allowed(subject_id, origin, deployment["subject_pattern"]):
                return None
            rows = conn.execute(
                """
                SELECT * FROM review_access_codes
                WHERE project_id = ?
                  AND active = 1
                  AND (deployment_id IS NULL OR deployment_id = ?)
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (project["id"], deployment["id"], isoformat(now)),
            ).fetchall()
            matched: sqlite3.Row | None = None
            requested_email = (email or "").strip().lower()
            for row in rows:
                row_email = str(row["email"] or "").strip().lower()
                if row_email and row_email != requested_email:
                    continue
                if verify_access_code(access_code, row["code_hash"]):
                    matched = row
                    break
            if matched is None:
                return None
            token = "rev_" + secrets.token_urlsafe(32)
            session_id = "rev_sess_" + uuid.uuid4().hex
            expires_at = now + timedelta(seconds=self.session_ttl_seconds)
            conn.execute(
                """
                INSERT INTO review_sessions (
                    id, project_id, deployment_id, access_code_id, token_hash,
                    subject_label, origin, created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    session_id,
                    project["id"],
                    deployment["id"],
                    matched["id"],
                    hash_session_token(token),
                    matched["label"],
                    origin,
                    isoformat(now),
                    isoformat(expires_at),
                ),
            )
            return {
                "session_id": session_id,
                "token": token,
                "expires_at": isoformat(expires_at),
                "project_id": project["id"],
                "deployment_id": deployment["id"],
                "label": matched["label"],
            }

    def validate_token(
        self,
        *,
        token: str,
        origin: str,
        project_identifier: str | None = None,
        deployment_identifier: str | None = None,
        subject_id: str | None = None,
    ) -> ReviewAuthSession | None:
        now = isoformat(utc_now())
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    s.id AS session_id,
                    s.project_id,
                    s.deployment_id,
                    s.access_code_id,
                    s.subject_label,
                    s.origin,
                    s.expires_at,
                    p.client_id,
                    p.slug AS project_slug,
                    d.slug AS deployment_slug,
                    d.allowed_origin,
                    d.subject_pattern,
                    d.active AS deployment_active
                FROM review_sessions s
                JOIN projects p ON p.id = s.project_id
                JOIN review_deployments d ON d.id = s.deployment_id
                WHERE s.token_hash = ?
                  AND s.revoked_at IS NULL
                  AND s.expires_at > ?
                LIMIT 1
                """,
                (hash_session_token(token), now),
            ).fetchone()
            if row is None:
                return None
            if row["origin"] != origin or row["allowed_origin"] != origin or int(row["deployment_active"]) != 1:
                return None
            if project_identifier and project_identifier not in (row["project_id"], row["project_slug"]):
                return None
            if deployment_identifier and deployment_identifier not in (row["deployment_id"], row["deployment_slug"]):
                return None
            if subject_id is not None and not subject_allowed(subject_id, origin, row["subject_pattern"]):
                return None
            return ReviewAuthSession(
                session_id=row["session_id"],
                client_id=row["client_id"],
                project_id=row["project_id"],
                deployment_id=row["deployment_id"],
                access_code_id=row["access_code_id"],
                label=row["subject_label"] or "",
                origin=row["origin"],
                expires_at=row["expires_at"],
                deployment_slug=row["deployment_slug"],
                project_slug=row["project_slug"],
                subject_pattern=row["subject_pattern"],
            )
