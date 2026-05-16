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
from typing import Any
from urllib.parse import urlparse

from libs.common.logging import get_logger

_CODE_HASH_PREFIX = "pbkdf2_sha256"
_CODE_HASH_ITERATIONS = 210_000
_DEPLOY_HOOK_TOKEN_PREFIX = "rdh_"

log = get_logger()


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


def hash_deploy_hook_token(secret: str, *, salt: bytes | None = None) -> str:
    return hash_access_code(secret, salt=salt)


def verify_deploy_hook_token(secret: str, stored_hash: str) -> bool:
    return verify_access_code(secret, stored_hash)


def parse_deploy_hook_token(token: str) -> tuple[str, str] | None:
    if not token.startswith(_DEPLOY_HOOK_TOKEN_PREFIX):
        return None
    payload = token[len(_DEPLOY_HOOK_TOKEN_PREFIX):]
    hook_id, sep, secret = payload.rpartition("_")
    if not sep or not hook_id or not secret:
        return None
    return hook_id, secret


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
    class PostgresConnection:
        def __init__(self, raw_conn: Any) -> None:
            self.raw_conn = raw_conn

        def __enter__(self) -> "ReviewAuthStore.PostgresConnection":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            if exc_type is None:
                self.raw_conn.commit()
            else:
                self.raw_conn.rollback()
            self.raw_conn.close()

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
            return self.raw_conn.execute(self._translate_placeholders(sql), params)

        def executescript(self, sql: str) -> None:
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    self.execute(statement)

        @staticmethod
        def _translate_placeholders(sql: str) -> str:
            return sql.replace("?", "%s")

    Connection = PostgresConnection

    class SQLiteConnection:
        def __init__(self, raw_conn: sqlite3.Connection) -> None:
            self.raw_conn = raw_conn
            self.raw_conn.row_factory = sqlite3.Row

        def __enter__(self) -> "ReviewAuthStore.SQLiteConnection":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            if exc_type is None:
                self.raw_conn.commit()
            else:
                self.raw_conn.rollback()
            self.raw_conn.close()

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
            return self.raw_conn.execute(sql, params)

        def executescript(self, sql: str) -> None:
            self.raw_conn.executescript(sql)

    def __init__(self, *, backend: str, dsn: str, db_path: str, session_ttl_seconds: int) -> None:
        self.backend = backend.strip().lower() or "sqlite"
        self.dsn = dsn.strip()
        self.db_path = db_path.strip() or "data/clients.db"
        if self.backend == "postgres" and not self.dsn:
            raise ValueError("Postgres review auth requires CLIENTS_DATABASE_URL or CLIENTS_PG_* settings")
        if self.backend not in {"sqlite", "postgres"}:
            raise ValueError(f"unsupported review auth backend: {backend}")
        self.session_ttl_seconds = int(session_ttl_seconds)
        self.init_db()

    def connect(self) -> "ReviewAuthStore.PostgresConnection | ReviewAuthStore.SQLiteConnection":
        if self.backend == "sqlite":
            path = os.path.abspath(self.db_path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            raw_conn = sqlite3.connect(path)
            raw_conn.execute("PRAGMA foreign_keys = ON")
            return self.SQLiteConnection(raw_conn)
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - exercised only without optional dep installed
            raise RuntimeError("Postgres review auth backend requires the psycopg package.") from exc
        return self.PostgresConnection(psycopg.connect(self.dsn, row_factory=dict_row))

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY,
                    slug TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    rolodex_entry_path TEXT,
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
                CREATE TABLE IF NOT EXISTS review_deploy_hooks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    label TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    allowed_host_suffixes TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    last_used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_review_sessions_token_hash ON review_sessions(token_hash);
                CREATE INDEX IF NOT EXISTS idx_review_access_codes_project_deployment ON review_access_codes(project_id, deployment_id);
                """
            )
            if self.backend == "sqlite":
                client_columns = {row[1] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
                deployment_columns = {row[1] for row in conn.execute("PRAGMA table_info(review_deployments)").fetchall()}
            else:
                client_columns = {
                    row["column_name"]
                    for row in conn.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'clients'
                        """
                    ).fetchall()
                }
                deployment_columns = {
                    row["column_name"]
                    for row in conn.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'review_deployments'
                        """
                    ).fetchall()
                }
            if "rolodex_entry_path" not in client_columns:
                conn.execute("ALTER TABLE clients ADD COLUMN rolodex_entry_path TEXT")
            if "vercel_deployment_id" not in deployment_columns:
                conn.execute("ALTER TABLE review_deployments ADD COLUMN vercel_deployment_id TEXT")
            if "commit_sha" not in deployment_columns:
                conn.execute("ALTER TABLE review_deployments ADD COLUMN commit_sha TEXT")
            if "updated_at" not in deployment_columns:
                conn.execute("ALTER TABLE review_deployments ADD COLUMN updated_at TEXT")

    def validate_deploy_hook_token(self, *, token: str, project_identifier: str) -> dict[str, Any] | None:
        if not token.startswith(_DEPLOY_HOOK_TOKEN_PREFIX):
            return None
        now = isoformat(utc_now())
        with self.connect() as conn:
            project = self._get_project(conn, project_identifier)
            if project is None:
                return None
            rows = conn.execute(
                """
                SELECT * FROM review_deploy_hooks
                WHERE project_id = ?
                  AND active = 1
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (project["id"], now),
            ).fetchall()
            for row in rows:
                token_prefix = f"{_DEPLOY_HOOK_TOKEN_PREFIX}{row['id']}_"
                if not token.startswith(token_prefix):
                    continue
                secret = token[len(token_prefix):]
                if not secret or not verify_deploy_hook_token(secret, row["token_hash"]):
                    continue
                conn.execute("UPDATE review_deploy_hooks SET last_used_at = ? WHERE id = ?", (now, row["id"]))
                return {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "label": row["label"],
                    "allowed_host_suffixes": row["allowed_host_suffixes"],
                }
            return None

    def register_deployment(
        self,
        *,
        project_identifier: str,
        deployment_slug: str,
        branch: str,
        allowed_origin: str,
        subject_pattern: str,
        vercel_deployment_id: str | None = None,
        commit_sha: str | None = None,
    ) -> dict[str, Any] | None:
        now = isoformat(utc_now())
        with self.connect() as conn:
            project = self._get_project(conn, project_identifier)
            if project is None:
                return None
            deployment_id = deployment_slug
            conn.execute(
                """
                INSERT INTO review_deployments
                    (id, project_id, slug, branch, allowed_origin, subject_pattern, active, created_at,
                     vercel_deployment_id, commit_sha, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
                    slug = excluded.slug,
                    branch = excluded.branch,
                    allowed_origin = excluded.allowed_origin,
                    subject_pattern = excluded.subject_pattern,
                    active = 1,
                    vercel_deployment_id = excluded.vercel_deployment_id,
                    commit_sha = excluded.commit_sha,
                    updated_at = excluded.updated_at
                """,
                (
                    deployment_id,
                    project["id"],
                    deployment_slug,
                    branch,
                    allowed_origin,
                    subject_pattern,
                    now,
                    vercel_deployment_id,
                    commit_sha,
                    now,
                ),
            )
            return {
                "id": deployment_id,
                "slug": deployment_slug,
                "project_id": project["id"],
                "branch": branch,
                "allowed_origin": allowed_origin,
                "subject_pattern": subject_pattern,
                "active": True,
                "vercel_deployment_id": vercel_deployment_id,
                "commit_sha": commit_sha,
                "updated_at": now,
            }

    def _get_project(self, conn: Any, project_identifier: str) -> Any | None:
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

    def _get_deployment(self, conn: Any, project_id: str, deployment_identifier: str) -> Any | None:
        return conn.execute(
            """
            SELECT * FROM review_deployments
            WHERE project_id = ? AND (id = ? OR slug = ?) AND active = 1
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (project_id, deployment_identifier, deployment_identifier, deployment_identifier),
        ).fetchone()

    def rotate_access_code(
        self,
        *,
        client_id: str,
        client_slug: str,
        client_name: str,
        rolodex_entry_path: str | None,
        project_id: str,
        project_slug: str,
        project_name: str,
        deployment_id: str | None,
        deployment_slug: str | None,
        allowed_origin: str | None,
        subject_pattern: str | None,
        access_code_id: str,
        access_label: str,
        access_code: str,
        access_email: str | None = None,
        deployment_scoped_access: bool = False,
    ) -> dict[str, Any]:
        now = isoformat(utc_now())
        cleaned_deployment_id = (deployment_id or "").strip() or None
        cleaned_deployment_slug = (deployment_slug or cleaned_deployment_id or "").strip() or None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO clients (id, slug, name, rolodex_entry_path, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    slug = excluded.slug,
                    name = excluded.name,
                    rolodex_entry_path = excluded.rolodex_entry_path
                """,
                (client_id, client_slug, client_name, rolodex_entry_path, now),
            )
            conn.execute(
                """
                INSERT INTO projects (id, client_id, slug, name, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    client_id = excluded.client_id,
                    slug = excluded.slug,
                    name = excluded.name
                """,
                (project_id, client_id, project_slug, project_name, now),
            )
            if cleaned_deployment_id and allowed_origin and subject_pattern:
                conn.execute(
                    """
                    INSERT INTO review_deployments
                        (id, project_id, slug, branch, allowed_origin, subject_pattern, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        project_id = excluded.project_id,
                        slug = excluded.slug,
                        branch = excluded.branch,
                        allowed_origin = excluded.allowed_origin,
                        subject_pattern = excluded.subject_pattern,
                        active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        cleaned_deployment_id,
                        project_id,
                        cleaned_deployment_slug or cleaned_deployment_id,
                        "operator",
                        allowed_origin,
                        subject_pattern,
                        now,
                        now,
                    ),
                )
            access_deployment_id = cleaned_deployment_id if deployment_scoped_access else None
            conn.execute(
                """
                INSERT INTO review_access_codes
                    (id, project_id, deployment_id, label, email, code_hash, active, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
                    deployment_id = excluded.deployment_id,
                    label = excluded.label,
                    email = excluded.email,
                    code_hash = excluded.code_hash,
                    active = 1,
                    expires_at = NULL
                """,
                (
                    access_code_id,
                    project_id,
                    access_deployment_id,
                    access_label,
                    (access_email or "").strip() or None,
                    hash_access_code(access_code),
                    now,
                ),
            )
        return {
            "client_id": client_id,
            "project_id": project_id,
            "deployment_id": cleaned_deployment_id,
            "access_code_id": access_code_id,
            "access_label": access_label,
            "project_scoped_access": access_deployment_id is None,
            "email_configured": bool((access_email or "").strip()),
            "active": True,
            "last_rotated_at": now,
        }

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
        email_present = bool((email or "").strip())
        with self.connect() as conn:
            project = self._get_project(conn, project_identifier)
            if project is None:
                log.warning(
                    "review_auth_session_rejected",
                    reason="project_not_found",
                    project_identifier=project_identifier,
                    deployment_identifier=deployment_identifier,
                    origin=origin,
                    subject_id=subject_id,
                    email_present=email_present,
                )
                return None
            deployment = self._get_deployment(conn, str(project["id"]), deployment_identifier)
            if deployment is None:
                log.warning(
                    "review_auth_session_rejected",
                    reason="deployment_not_found",
                    project_id=str(project["id"]),
                    project_identifier=project_identifier,
                    deployment_identifier=deployment_identifier,
                    origin=origin,
                    subject_id=subject_id,
                    email_present=email_present,
                )
                return None
            allowed_origin = str(deployment["allowed_origin"])
            if allowed_origin != origin:
                log.warning(
                    "review_auth_session_rejected",
                    reason="origin_mismatch",
                    project_id=str(project["id"]),
                    deployment_id=str(deployment["id"]),
                    deployment_identifier=deployment_identifier,
                    origin=origin,
                    allowed_origin=allowed_origin,
                    subject_id=subject_id,
                    email_present=email_present,
                )
                return None
            if not subject_allowed(subject_id, origin, deployment["subject_pattern"]):
                log.warning(
                    "review_auth_session_rejected",
                    reason="subject_not_allowed",
                    project_id=str(project["id"]),
                    deployment_id=str(deployment["id"]),
                    deployment_identifier=deployment_identifier,
                    origin=origin,
                    subject_id=subject_id,
                    subject_pattern=deployment["subject_pattern"],
                    email_present=email_present,
                )
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
            matched: Any | None = None
            requested_email = (email or "").strip().lower()
            skipped_email_mismatch = 0
            for row in rows:
                row_email = str(row["email"] or "").strip().lower()
                if row_email and row_email != requested_email:
                    skipped_email_mismatch += 1
                    continue
                if verify_access_code(access_code, row["code_hash"]):
                    matched = row
                    break
            if matched is None:
                log.warning(
                    "review_auth_session_rejected",
                    reason="access_code_no_match",
                    project_id=str(project["id"]),
                    deployment_id=str(deployment["id"]),
                    deployment_identifier=deployment_identifier,
                    origin=origin,
                    subject_id=subject_id,
                    email_present=bool(requested_email),
                    eligible_access_code_rows=len(rows),
                    email_mismatch_rows=skipped_email_mismatch,
                    candidate_access_code_ids=[str(row["id"]) for row in rows],
                    candidate_deployment_scopes=[str(row["deployment_id"] or "") for row in rows],
                    candidate_email_bound=[bool(str(row["email"] or "").strip()) for row in rows],
                )
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
            log.info(
                "review_auth_session_created",
                project_id=str(project["id"]),
                deployment_id=str(deployment["id"]),
                access_code_id=str(matched["id"]),
                origin=origin,
                subject_id=subject_id,
                email_present=bool(requested_email),
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


def create_review_auth_store(
    *,
    postgres_dsn: str,
    session_ttl_seconds: int,
    backend: str = "postgres",
    db_path: str = "data/clients.db",
) -> ReviewAuthStore:
    return ReviewAuthStore(
        backend=backend,
        dsn=postgres_dsn,
        db_path=db_path,
        session_ttl_seconds=session_ttl_seconds,
    )
