"""
Cases service — SQLite-backed HTTP API for case/step/slot/log persistence.

Frank creates cases here when he dispatches work. Steps advance through
PENDING → READY → RUNNING → COMPLETED/FAILED/SKIPPED. Case status tracks the
assignment lifecycle separately: OPEN → READY → IN_PROGRESS → BLOCKED |
COMPLETED | FAILED. Slots carry named I/O values between steps, including
execution-run provenance for audit/debugging. Logs provide an audit trail.

Environment variables:
  CASES_HTTP_PORT   default: 8083
  CASES_DB_PATH     default: /data/cases.db
  LOG_LEVEL         default: info
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .contract import ProcessContractError, compile_process_contract

log = logging.getLogger("cases")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())

DB_PATH = os.environ.get("CASES_DB_PATH", "/data/cases.db")


class SlotWriteConflictError(ValueError):
    """Raised when a populated slot is rewritten with a different value."""

# ── Real-time broadcast ───────────────────────────────────────────────────────
# Each open SSE connection registers a queue here. Mutations call _broadcast()
# which enqueues an event for every subscriber watching that case_id.

_subscribers: dict[str, list[asyncio.Queue]] = {}


def _broadcast(case_id: str, event: str) -> None:
    for q in _subscribers.get(case_id, []):
        q.put_nowait(event)


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _safe_json_size(payload: Any) -> int | None:
    try:
        return len(json.dumps(payload, default=str))
    except Exception:
        return None


# ── Database ──────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "cases", "process_source", "TEXT")
    _ensure_column(conn, "cases", "process_hash", "TEXT")
    _ensure_column(conn, "cases", "contract_json", "TEXT")
    _ensure_column(conn, "cases", "dispatch_packet_json", "TEXT")
    _ensure_column(conn, "case_steps", "completed_at", "TEXT")
    _ensure_column(conn, "case_steps", "runtime_state_json", "TEXT")
    _ensure_column(conn, "case_steps", "runtime_updated_at", "TEXT")
    _ensure_column(conn, "case_slots", "agent_run_id", "TEXT")
    _ensure_column(conn, "case_slots", "produced_at", "TEXT")
    conn.execute("UPDATE cases SET status = 'IN_PROGRESS' WHERE status = 'RUNNING'")
    conn.commit()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cases (
          id               TEXT PRIMARY KEY,
          queue_message_id TEXT NOT NULL,
          process_name     TEXT,
          process_path     TEXT,
          process_source   TEXT,
          process_hash     TEXT,
          contract_json    TEXT,
          dispatch_packet_json TEXT,
          title            TEXT NOT NULL,
          objective        TEXT NOT NULL,
          sender           TEXT NOT NULL,
          status           TEXT NOT NULL DEFAULT 'OPEN',
          created_at       TEXT NOT NULL,
          claimed_at       TEXT,
          completed_at     TEXT
        );


        CREATE TABLE IF NOT EXISTS case_steps (
          id          TEXT PRIMARY KEY,
          case_id     TEXT NOT NULL REFERENCES cases(id),
          idx         INTEGER NOT NULL,
          step_id     TEXT NOT NULL,
          name        TEXT NOT NULL,
          executor    TEXT,
          action      TEXT NOT NULL,
          args_json   TEXT NOT NULL DEFAULT '{}',
          status      TEXT NOT NULL DEFAULT 'PENDING',
          result_json TEXT,
          runtime_state_json TEXT,
          created_at  TEXT NOT NULL,
          updated_at  TEXT NOT NULL,
          runtime_updated_at TEXT,
          completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS case_slots (
          id        TEXT PRIMARY KEY,
          case_id   TEXT NOT NULL REFERENCES cases(id),
          name      TEXT NOT NULL,
          value     TEXT,
          filled_at TEXT,
          agent_run_id TEXT,
          produced_at TEXT,
          UNIQUE(case_id, name)
        );

        CREATE TABLE IF NOT EXISTS case_logs (
          id            TEXT PRIMARY KEY,
          case_id       TEXT NOT NULL REFERENCES cases(id),
          step_id       TEXT,
          type          TEXT NOT NULL,
          message       TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_task_audits (
          id                 TEXT PRIMARY KEY,
          case_id            TEXT NOT NULL REFERENCES cases(id),
          step_id            TEXT NOT NULL,
          step_db_row_id     TEXT,
          idempotency_key    TEXT NOT NULL,
          reference_json     TEXT NOT NULL,
          audit_record_json  TEXT NOT NULL,
          created_at         TEXT NOT NULL,
          updated_at         TEXT NOT NULL,
          UNIQUE(case_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS case_runs (
          id              TEXT PRIMARY KEY,
          case_id         TEXT NOT NULL REFERENCES cases(id),
          runtime_mode    TEXT NOT NULL,
          runner          TEXT NOT NULL,
          status          TEXT NOT NULL,
          idempotency_key TEXT,
          metadata_json   TEXT NOT NULL DEFAULT '{}',
          created_at      TEXT NOT NULL,
          started_at      TEXT,
          updated_at      TEXT NOT NULL,
          ended_at        TEXT,
          UNIQUE(case_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS step_runs (
          id              TEXT PRIMARY KEY,
          case_run_id     TEXT NOT NULL REFERENCES case_runs(id),
          case_id         TEXT NOT NULL REFERENCES cases(id),
          step_id         TEXT NOT NULL,
          step_db_row_id  TEXT NOT NULL,
          idx             INTEGER,
          title           TEXT,
          executor_type   TEXT NOT NULL,
          status          TEXT NOT NULL,
          idempotency_key TEXT,
          metadata_json   TEXT NOT NULL DEFAULT '{}',
          created_at      TEXT NOT NULL,
          started_at      TEXT,
          updated_at      TEXT NOT NULL,
          ended_at        TEXT,
          UNIQUE(case_run_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS execution_spans (
          id             TEXT PRIMARY KEY,
          case_run_id    TEXT NOT NULL REFERENCES case_runs(id),
          step_run_id    TEXT REFERENCES step_runs(id),
          parent_span_id TEXT REFERENCES execution_spans(id),
          name           TEXT NOT NULL,
          status         TEXT NOT NULL,
          metadata_json  TEXT NOT NULL DEFAULT '{}',
          created_at     TEXT NOT NULL,
          started_at     TEXT,
          updated_at     TEXT NOT NULL,
          ended_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS execution_events (
          id            TEXT PRIMARY KEY,
          case_id       TEXT NOT NULL REFERENCES cases(id),
          case_run_id   TEXT NOT NULL REFERENCES case_runs(id),
          step_run_id   TEXT REFERENCES step_runs(id),
          span_id       TEXT REFERENCES execution_spans(id),
          type          TEXT NOT NULL,
          severity      TEXT NOT NULL DEFAULT 'info',
          message       TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS execution_artifacts (
          id               TEXT PRIMARY KEY,
          case_id          TEXT NOT NULL REFERENCES cases(id),
          case_run_id      TEXT NOT NULL REFERENCES case_runs(id),
          step_run_id      TEXT REFERENCES step_runs(id),
          span_id          TEXT REFERENCES execution_spans(id),
          role             TEXT NOT NULL,
          uri              TEXT NOT NULL,
          sha256           TEXT,
          size_bytes       INTEGER,
          content_type     TEXT,
          redaction_status TEXT NOT NULL DEFAULT 'not_applicable',
          metadata_json    TEXT NOT NULL DEFAULT '{}',
          created_at       TEXT NOT NULL
        );


        CREATE INDEX IF NOT EXISTS idx_cases_status    ON cases(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_cases_sender    ON cases(sender, status);
        CREATE INDEX IF NOT EXISTS idx_cases_queue_message ON cases(queue_message_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_steps_case      ON case_steps(case_id, idx);
        CREATE INDEX IF NOT EXISTS idx_slots_case      ON case_slots(case_id, name);
        CREATE INDEX IF NOT EXISTS idx_logs_case       ON case_logs(case_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_model_task_audits_case ON model_task_audits(case_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_case_runs_case ON case_runs(case_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_case_runs_status ON case_runs(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_step_runs_case_run ON step_runs(case_run_id, idx, created_at);
        CREATE INDEX IF NOT EXISTS idx_step_runs_step ON step_runs(case_id, step_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_execution_spans_step ON execution_spans(step_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_execution_events_case_run ON execution_events(case_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_execution_events_step ON execution_events(step_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_execution_artifacts_step ON execution_artifacts(step_run_id, created_at);
        """)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_row(conn: sqlite3.Connection, artifact_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM execution_artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "artifact not found")
    return row


def _artifact_allowed_roots() -> list[Path]:
    raw_values = [
        os.environ.get("FRANK_EXECUTION_ROOT", ""),
        os.environ.get("STT_ALLOWED_AUDIO_ROOTS", ""),
        os.environ.get("CASES_ARTIFACT_ALLOWED_ROOTS", ""),
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_values:
        for part in str(raw or "").split(os.pathsep):
            value = part.strip()
            if not value:
                continue
            try:
                resolved = Path(value).expanduser().resolve(strict=False)
            except OSError:
                continue
            key = str(resolved)
            if key not in seen:
                seen.add(key)
                roots.append(resolved)
    return roots


def _mirror_allowed_roots() -> list[Path]:
    raw = os.environ.get("CASES_MIRROR_ALLOWED_ROOTS", "/data")
    roots: list[Path] = []
    seen: set[str] = set()
    for part in str(raw or "").split(os.pathsep):
        value = part.strip()
        if not value:
            continue
        try:
            resolved = Path(value).expanduser().resolve(strict=False)
        except OSError:
            continue
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            roots.append(resolved)
    return roots


def _decode_mirror_path(encoded_path: str) -> str:
    try:
        padded = encoded_path + "=" * (-len(encoded_path) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(422, "mirror path is not valid base64url") from exc


def _mirror_filesystem_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.startswith("/"):
        raise HTTPException(422, "mirror path must be absolute")
    try:
        resolved = Path(raw_path).expanduser().resolve(strict=False)
    except OSError as exc:
        raise HTTPException(422, f"mirror path is invalid: {exc}") from None
    roots = _mirror_allowed_roots()
    if not roots:
        raise HTTPException(503, "mirror roots are not configured")
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise HTTPException(403, "mirror path is outside configured mirror roots")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(404, "mirror backing file not found")
    return resolved


def _artifact_filesystem_path(row: sqlite3.Row) -> Path:
    uri = str(row["uri"] or "").strip()
    if uri.startswith("dir:"):
        raw_path = uri[4:]
    elif uri.startswith("file:"):
        raw_path = uri[5:]
    else:
        raise HTTPException(415, "artifact URI is not a local file artifact")
    if not raw_path:
        raise HTTPException(422, "artifact URI has no path")
    try:
        resolved = Path(raw_path).expanduser().resolve(strict=False)
    except OSError as exc:
        raise HTTPException(422, f"artifact path is invalid: {exc}") from None
    roots = _artifact_allowed_roots()
    if not roots:
        raise HTTPException(503, "artifact content roots are not configured")
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise HTTPException(403, "artifact path is outside configured artifact roots")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(404, "artifact backing file not found")
    return resolved


def _artifact_content_type(row: sqlite3.Row, path: Path) -> str:
    declared = str(row["content_type"] or "").strip()
    if declared:
        return declared
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _write_case_log(
    conn: sqlite3.Connection,
    case_id: str,
    log_type: str,
    message: str,
    *,
    step_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO case_logs (id, case_id, step_id, type, message, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            new_id("log"),
            case_id,
            step_id,
            log_type,
            message,
            json.dumps(metadata or {}),
            now(),
        ),
    )



_SECRET_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "oauth",
    "auth",
    "authorization",
    "token",
    "secret",
    "password",
    "connection_string",
    "raw_secret_env",
    "credential",
    "credentials",
)

_ARTIFACT_FIELDS = {
    "prompt": "prompt_artifact",
    "final_response": "final_response_artifact",
    "tool_calls": "tool_calls_artifact",
    "completion_metadata": "completion_metadata_artifact",
}

_HASH_FIELDS = {
    "prompt": "prompt_sha256",
    "task_artifact": "task_artifact_sha256",
    "final_response": "final_response_sha256",
    "tool_calls": "tool_calls_sha256",
    "completion_metadata": "completion_metadata_sha256",
}


_SAFE_AUDIT_RECORD_FIELDS = {
    "case_id",
    "step_id",
    "step_db_row_id",
    "kanban_task_id",
    "hermes_run_id",
    "profile",
    "provider",
    "model",
    "hermes_home",
    "workspace",
    "outcome",
    "status",
    *_ARTIFACT_FIELDS.values(),
    *_HASH_FIELDS.values(),
}


def _safe_audit_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in sorted(_SAFE_AUDIT_RECORD_FIELDS) if key in record}


def _looks_like_secret_value(value: str) -> bool:
    lowered = value.lower()
    return "bearer " in lowered or lowered.startswith(("sk-", "xoxb-", "xoxp-"))


def _reject_secret_like_fields(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS):
                dotted = ".".join((*path, str(key)))
                raise ValueError(f"secret-like audit field rejected: {dotted}")
            _reject_secret_like_fields(nested, (*path, str(key)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_secret_like_fields(nested, (*path, str(index)))
    elif isinstance(value, str) and _looks_like_secret_value(value):
        dotted = ".".join(path) or "<value>"
        raise ValueError(f"secret-like audit field rejected: {dotted}")


def _audit_stable_key(case_id: str, step_id: str, record: dict[str, Any]) -> str:
    hermes_run_id = str(record.get("hermes_run_id") or "").strip()
    if hermes_run_id:
        return f"hermes_run:{hermes_run_id}"
    required = {
        "step_db_row_id": record.get("step_db_row_id"),
        "profile": record.get("profile"),
        "task_artifact_sha256": record.get("task_artifact_sha256"),
    }
    missing = [key for key, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError("pre-run audit key requires " + ", ".join(missing))
    basis = {
        "case_id": case_id,
        "step_db_row_id": required["step_db_row_id"],
        "profile": required["profile"],
        "task_artifact_sha256": required["task_artifact_sha256"],
    }
    encoded = json.dumps(basis, sort_keys=True, separators=(",", ":"))
    return "pre_run:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _audit_reference(case_id: str, step_id: str, stable_key: str, record: dict[str, Any], *, status: str) -> dict[str, Any]:
    artifact_paths = {name: record[field] for name, field in _ARTIFACT_FIELDS.items() if field in record}
    artifact_hashes = {name: record[field] for name, field in _HASH_FIELDS.items() if field in record}
    id_basis = json.dumps({"case_id": case_id, "step_id": step_id, "idempotency_key": stable_key}, sort_keys=True, separators=(",", ":"))
    audit_record_id = "audit_" + hashlib.sha256(id_basis.encode("utf-8")).hexdigest()[:16]
    reference = {
        "audit_record_id": audit_record_id,
        "idempotency_key": stable_key,
        "upsert_status": status,
        "case_id": case_id,
        "step_id": step_id,
        "step_db_row_id": record.get("step_db_row_id"),
        "kanban_task_id": record.get("kanban_task_id"),
        "hermes_run_id": record.get("hermes_run_id"),
        "artifact_paths": artifact_paths,
        "artifact_hashes": artifact_hashes,
    }
    return {key: value for key, value in reference.items() if value not in (None, {}, [])}


def _reject_audit_collision(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    for field in ("case_id", "step_id", "step_db_row_id", "kanban_task_id", "hermes_run_id"):
        if field in existing and field in incoming and existing[field] != incoming[field]:
            raise ValueError(f"audit upsert collision: {field}")
    existing_hashes = existing.get("artifact_hashes") or {}
    incoming_hashes = incoming.get("artifact_hashes") or {}
    for field, value in existing_hashes.items():
        if field in incoming_hashes and incoming_hashes[field] != value:
            raise ValueError(f"audit upsert collision: artifact_hashes.{field}")


def _validate_audit_identity(case_id: str, step_id: str, record: dict[str, Any]) -> None:
    if "case_id" in record and record["case_id"] != case_id:
        raise ValueError("audit record identity mismatch: case_id")
    if "step_id" in record and record["step_id"] != step_id:
        raise ValueError("audit record identity mismatch: step_id")


def _audit_rows(conn: sqlite3.Connection, case_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM model_task_audits WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["reference_json"] = json.loads(item["reference_json"])
        item["audit_record_json"] = json.loads(item["audit_record_json"])
        result.append(item)
    return result


def _parse_contract(row: sqlite3.Row) -> dict[str, Any] | None:
    raw = row["contract_json"]
    if not raw:
        return None
    return json.loads(raw)


def _parse_dispatch_packet(row: sqlite3.Row) -> dict[str, Any] | None:
    raw = row["dispatch_packet_json"]
    if not raw:
        return None
    return json.loads(raw)


def _case_creation_response(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    case_id = row["id"]
    steps = [_step_row_to_dict(step) for step in _step_rows(conn, case_id)]
    return {
        "case_id": case_id,
        "status": row["status"],
        "created_at": row["created_at"],
        "contract": _parse_contract(row),
        "steps": steps,
        "progress": _build_progress(steps),
    }


def _load_case_row(conn: sqlite3.Connection, case_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not row:
        raise HTTPException(404, "case not found")
    return row


def _contract_step(contract: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in contract.get("steps", []):
        if step.get("step_id") == step_id:
            return step
    raise KeyError(step_id)


def _create_contract_steps(
    conn: sqlite3.Connection,
    case_id: str,
    contract: dict[str, Any],
    created_at: str,
) -> list[dict[str, Any]]:
    created_steps: list[dict[str, Any]] = []
    for step in contract["steps"]:
        step_db_id = new_id("step")
        conn.execute(
            "INSERT INTO case_steps (id, case_id, idx, step_id, name, executor, action, args_json, status, runtime_state_json, created_at, updated_at, runtime_updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, ?, ?, NULL, NULL)",
            (
                step_db_id,
                case_id,
                int(step["number"]) - 1,
                step["step_id"],
                step["title"],
                step.get("executor"),
                step["action"],
                json.dumps({}),
                created_at,
                created_at,
            ),
        )
        created_steps.append(
            {
                "id": step_db_id,
                "case_id": case_id,
                "idx": int(step["number"]) - 1,
                "step_id": step["step_id"],
                "name": step["title"],
                "executor": step.get("executor"),
                "action": step["action"],
                "status": "PENDING",
                "runtime_state_json": None,
                "runtime_updated_at": None,
            }
        )
    return created_steps


def _create_contract_slots(conn: sqlite3.Connection, case_id: str, contract: dict[str, Any]) -> None:
    for slot_name in contract["slot_names"]:
        conn.execute(
            "INSERT INTO case_slots (id, case_id, name, value, filled_at, agent_run_id, produced_at) "
            "VALUES (?, ?, ?, NULL, NULL, NULL, NULL)",
            (new_id("slot"), case_id, slot_name),
        )


def _serialize_json_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _slot_write_state(row: sqlite3.Row | None, value: Any) -> str:
    if row is None or row["value"] is None:
        return "write"
    incoming = _serialize_json_value(value)
    existing = str(row["value"])
    return "idempotent" if incoming == existing else "conflict"


def _write_slot_value_once(
    conn: sqlite3.Connection,
    case_id: str,
    name: str,
    value: Any,
    filled_at: str,
    *,
    agent_run_id: str | None = None,
    produced_at: str | None = None,
) -> str:
    row = conn.execute(
        "SELECT * FROM case_slots WHERE case_id = ? AND name = ?",
        (case_id, name),
    ).fetchone()
    state = _slot_write_state(row, value)
    if state == "idempotent":
        return state
    if state == "conflict":
        raise SlotWriteConflictError(f"slot {name} is already populated and cannot be rewritten")

    serialized_value = _serialize_json_value(value)
    if row is None:
        conn.execute(
            "INSERT INTO case_slots (id, case_id, name, value, filled_at, agent_run_id, produced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("slot"),
                case_id,
                name,
                serialized_value,
                filled_at,
                agent_run_id,
                produced_at,
            ),
        )
    else:
        conn.execute(
            "UPDATE case_slots SET value = ?, filled_at = ?, agent_run_id = ?, produced_at = ? "
            "WHERE case_id = ? AND name = ?",
            (
                serialized_value,
                filled_at,
                agent_run_id,
                produced_at,
                case_id,
                name,
            ),
        )
    return state


STEP_STATUS_ALIASES = {
    "SUCCESS": "COMPLETED",
    "ERROR": "FAILED",
    "COMPLETE": "COMPLETED",
}

CASE_STATUS_ALIASES = {
    "COMPLETE": "COMPLETED",
    "ERROR": "FAILED",
    "RUNNING": "IN_PROGRESS",
}

TERMINAL_STEP_STATUSES = {"COMPLETED", "FAILED", "SKIPPED"}
TERMINAL_CASE_STATUSES = {"COMPLETED", "FAILED"}
CASE_RUN_STATUSES = {"queued", "running", "completed", "blocked", "failed", "cancelled"}
STEP_RUN_STATUSES = {"pending", "ready", "running", "completed", "blocked", "failed", "skipped"}
SPAN_STATUSES = {"running", "completed", "blocked", "failed"}
EVENT_SEVERITIES = {"debug", "info", "warning", "error"}


def _normalize_step_status(status: str | None) -> str | None:
    if status is None:
        return None
    normalized = status.strip().upper()
    return STEP_STATUS_ALIASES.get(normalized, normalized)


def _normalize_case_status(status: str) -> str:
    normalized = status.strip().upper()
    return CASE_STATUS_ALIASES.get(normalized, normalized)


def _normalize_observability_status(value: str, allowed: set[str], *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise HTTPException(422, f"invalid {field}: {value}")
    return normalized


def _json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _row_with_json(row: sqlite3.Row, *json_fields: str) -> dict[str, Any]:
    payload = dict(row)
    for field in json_fields:
        payload[field] = _json_dict(row[field])
    return payload


def _case_run_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM case_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "case run not found")
    return row


def _step_run_row(conn: sqlite3.Connection, step_run_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM step_runs WHERE id = ?", (step_run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "step run not found")
    return row


def _merge_metadata(existing_raw: str | None, patch: dict[str, Any] | None) -> dict[str, Any]:
    metadata = _json_dict(existing_raw)
    if patch:
        metadata.update(patch)
    return metadata


def _slot_rows_by_name(conn: sqlite3.Connection, case_id: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM case_slots WHERE case_id = ?", (case_id,)).fetchall()
    return {row["name"]: row for row in rows}


def _step_rows(conn: sqlite3.Connection, case_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM case_steps WHERE case_id = ? ORDER BY idx", (case_id,)).fetchall()


def _parse_runtime_state(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    return json.loads(raw)


def _step_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["runtime_state_json"] = _parse_runtime_state(row["runtime_state_json"])
    return data


def _slot_row_to_value(row: sqlite3.Row | None) -> Any:
    if not row or row["value"] is None:
        return None
    return json.loads(row["value"])


def _board_variable_value_summary(value: Any) -> dict[str, Any]:
    if value is None:
        return {"has_value": False, "value_preview": None, "value_kind": None, "value_size_bytes": 0}
    encoded = json.dumps(value, sort_keys=True)
    preview = encoded if len(encoded) <= 180 else encoded[:177] + "..."
    if isinstance(value, list):
        kind = "array"
    elif isinstance(value, dict):
        kind = "object"
    elif isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, (int, float)):
        kind = "number"
    elif isinstance(value, str):
        kind = "string"
    else:
        kind = type(value).__name__
    return {
        "has_value": True,
        "value_preview": preview,
        "value_kind": kind,
        "value_size_bytes": len(encoded.encode("utf-8")),
    }


def _board_variable_projection(
    *,
    case_id: str,
    contract: dict[str, Any],
    slots_by_name: dict[str, sqlite3.Row],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    steps_by_number = {step.get("number"): step for step in contract.get("steps", [])}
    producer_map = contract.get("producer_map") or {}
    consumer_map = contract.get("consumer_map") or {}
    variables_meta = contract.get("variables") or {}
    ordered_names = list(contract.get("slot_names") or variables_meta.keys())
    for name in slots_by_name:
        if name not in ordered_names:
            ordered_names.append(name)

    projected: list[dict[str, Any]] = []
    counts = {
        "dispatcher_input": 0,
        "produced_output": 0,
        "pending_output": 0,
        "deprecated_or_unreferenced": 0,
    }

    for name in ordered_names:
        slot = slots_by_name.get(name)
        value = _slot_row_to_value(slot)
        filled = slot is not None and slot["value"] is not None
        producer_number = producer_map.get(name)
        consumer_numbers = list(consumer_map.get(name) or [])
        producer_step = steps_by_number.get(producer_number)
        variable_contract = variables_meta.get(name) or {}
        in_contract = name in variables_meta or name in set(contract.get("slot_names") or [])
        value_summary = _board_variable_value_summary(value)

        if not in_contract or (producer_number is None and not consumer_numbers):
            category = "deprecated_or_unreferenced"
        elif producer_number is None:
            category = "dispatcher_input"
        elif filled:
            category = "produced_output"
        else:
            category = "pending_output"
        counts[category] += 1

        projected.append(
            {
                "case_id": case_id,
                "name": name,
                "category": category,
                "status": "filled" if filled else "pending",
                **value_summary,
                "filled_at": slot["filled_at"] if slot else None,
                "producer_step_number": producer_number,
                "producer_step_id": producer_step.get("step_id") if producer_step else None,
                "producer_step_title": producer_step.get("title") if producer_step else None,
                "consumer_step_numbers": consumer_numbers,
                "contract_type": variable_contract.get("type"),
                "description": variable_contract.get("description"),
                "in_current_contract": in_contract,
            }
        )

    return projected, counts


def _step_inputs_ready(step_contract: dict[str, Any], slots_by_name: dict[str, sqlite3.Row]) -> bool:
    for item in step_contract.get("input_items", []):
        row = slots_by_name.get(item["name"])
        if not row or row["value"] is None:
            return False
    return True


def _step_outputs_completed(step_contract: dict[str, Any], slots_by_name: dict[str, sqlite3.Row]) -> bool:
    outputs = step_contract.get("output_variables", [])
    if not outputs:
        return False
    for name in outputs:
        row = slots_by_name.get(name)
        if not row or row["value"] is None:
            return False
    return True


def _matches_variable_type(expected_type: str, value: Any) -> bool:
    normalized = expected_type.strip().lower()
    if normalized.startswith("string"):
        return isinstance(value, str)
    if normalized == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized == "boolean":
        return isinstance(value, bool)
    if normalized == "array":
        return isinstance(value, list)
    if normalized == "object":
        return isinstance(value, dict)
    return True


def _compute_case_status(
    current_status: str,
    steps: list[sqlite3.Row],
    slots_by_name: dict[str, sqlite3.Row],
) -> str:
    step_statuses = [str(step["status"]).upper() for step in steps]
    if any(status == "FAILED" for status in step_statuses):
        return "FAILED"
    if steps and all(status in {"COMPLETED", "SKIPPED"} for status in step_statuses):
        return "COMPLETED"
    normalized_current = _normalize_case_status(current_status)
    if any(status == "RUNNING" for status in step_statuses):
        return "IN_PROGRESS"
    if normalized_current in {"IN_PROGRESS", "READY", "BLOCKED"}:
        return normalized_current
    return "OPEN"


def _recompute_case_state(conn: sqlite3.Connection, case_id: str, *, t: str | None = None) -> None:
    case_row = _load_case_row(conn, case_id)
    contract = _parse_contract(case_row)
    if not contract:
        return

    timestamp = t or now()
    slots_by_name = _slot_rows_by_name(conn, case_id)
    steps = _step_rows(conn, case_id)

    for step_row in steps:
        step_contract = _contract_step(contract, step_row["step_id"])
        current_status = str(step_row["status"]).upper()
        next_status = current_status

        if current_status not in TERMINAL_STEP_STATUSES:
            if _step_outputs_completed(step_contract, slots_by_name):
                next_status = "COMPLETED"
            elif current_status == "PENDING" and _step_inputs_ready(step_contract, slots_by_name):
                next_status = "READY"

        if next_status != current_status:
            completed_at = timestamp if next_status == "COMPLETED" else step_row["completed_at"]
            conn.execute(
                "UPDATE case_steps SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                (next_status, timestamp, completed_at, step_row["id"]),
            )

    steps = _step_rows(conn, case_id)
    next_case_status = _compute_case_status(str(case_row["status"]).upper(), steps, slots_by_name)
    current_case_status = _normalize_case_status(str(case_row["status"]))
    if next_case_status != current_case_status:
        claimed_at = case_row["claimed_at"]
        if next_case_status == "IN_PROGRESS" and not claimed_at:
            claimed_at = timestamp
        completed_at = timestamp if next_case_status in TERMINAL_CASE_STATUSES else None
        conn.execute(
            "UPDATE cases SET status = ?, claimed_at = ?, completed_at = ? WHERE id = ?",
            (next_case_status, claimed_at, completed_at, case_id),
        )


def _build_progress(steps: list[dict[str, Any]]) -> dict[str, Any]:
    completed_steps = [step["id"] for step in steps if str(step["status"]).upper() == "COMPLETED"]
    failed_steps = [step["id"] for step in steps if str(step["status"]).upper() == "FAILED"]
    ready_steps = [step["id"] for step in steps if str(step["status"]).upper() == "READY"]
    running_steps = [
        step["id"]
        for step in steps
        if str(step["status"]).upper() == "RUNNING"
        or ((step.get("runtime_state_json") or {}).get("status") in {"active", "running"})
    ]
    return {
        "total_steps": len(steps),
        "completed_steps": completed_steps,
        "completed_step_count": len(completed_steps),
        "failed_steps": failed_steps,
        "ready_steps": ready_steps,
        "running_steps": running_steps,
    }


# ── Pydantic models ────────────────────────────────────────────────────────────

class CreateCaseIn(BaseModel):
    queue_message_id: str
    process_name: str | None = None
    process_path: str | None = None
    process_source: str
    title: str
    objective: str
    sender: str
    dispatch_packet_json: dict[str, Any] | None = None


class CreateStepsIn(BaseModel):
    steps: list[dict[str, Any]]


class UpdateStepIn(BaseModel):
    status: str | None = None
    result_json: dict[str, Any] | None = None
    agent_run_id: str | None = None


class UpdateStepRuntimeStateIn(BaseModel):
    runtime_state_json: dict[str, Any]


class UpsertSlotIn(BaseModel):
    name: str
    value: Any
    agent_run_id: str | None = None


class CreateLogIn(BaseModel):
    step_id: str | None = None
    type: str
    message: str
    metadata: dict[str, Any] | None = None


class UpdateStatusIn(BaseModel):
    status: str


class UpdateDispatchPacketIn(BaseModel):
    dispatch_packet_json: dict[str, Any]


class CompleteStepOutputsIn(BaseModel):
    outputs_json: dict[str, Any]
    agent_run_id: str | None = None
    notes: list[str] | None = None


class UpsertModelTaskAuditIn(BaseModel):
    step_id: str
    audit_record: dict[str, Any]


class CreateCaseRunIn(BaseModel):
    runtime_mode: str
    runner: str
    status: str = "running"
    idempotency_key: str | None = None
    metadata: dict[str, Any] | None = None


class UpdateCaseRunIn(BaseModel):
    status: str | None = None
    metadata: dict[str, Any] | None = None


class CreateStepRunIn(BaseModel):
    case_run_id: str
    step_id: str
    step_db_row_id: str
    idx: int | None = None
    title: str | None = None
    executor_type: str = "native"
    status: str = "running"
    idempotency_key: str | None = None
    metadata: dict[str, Any] | None = None


class UpdateStepRunIn(BaseModel):
    status: str | None = None
    metadata: dict[str, Any] | None = None


class CreateExecutionSpanIn(BaseModel):
    case_run_id: str
    step_run_id: str | None = None
    parent_span_id: str | None = None
    name: str
    status: str = "running"
    metadata: dict[str, Any] | None = None


class UpdateExecutionSpanIn(BaseModel):
    status: str | None = None
    metadata: dict[str, Any] | None = None


class CreateExecutionEventIn(BaseModel):
    case_run_id: str
    step_run_id: str | None = None
    span_id: str | None = None
    type: str
    severity: str = "info"
    message: str
    metadata: dict[str, Any] | None = None


class CreateExecutionArtifactIn(BaseModel):
    case_run_id: str
    step_run_id: str | None = None
    span_id: str | None = None
    role: str
    uri: str
    sha256: str | None = None
    size_bytes: int | None = None
    content_type: str | None = None
    redaction_status: str = "not_applicable"
    metadata: dict[str, Any] | None = None


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with get_db() as conn:
        _migrate(conn)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/cases", status_code=201)
def create_case(body: CreateCaseIn):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM cases WHERE queue_message_id = ? ORDER BY created_at ASC LIMIT 1",
            (body.queue_message_id,),
        ).fetchone()
        if existing:
            _recompute_case_state(conn, existing["id"])
            existing = _load_case_row(conn, existing["id"])
            response = _case_creation_response(conn, existing)
            response["reused"] = True
            return response

    try:
        contract = compile_process_contract(body.process_source, process_path=body.process_path)
    except ProcessContractError as exc:
        raise HTTPException(422, str(exc)) from exc

    case_id = new_id("case")
    t = now()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO cases (id, queue_message_id, process_name, process_path, process_source, process_hash, contract_json, dispatch_packet_json, title, objective, sender, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)",
            (
                case_id,
                body.queue_message_id,
                body.process_name,
                body.process_path,
                body.process_source,
                contract["process_hash"],
                json.dumps(contract),
                json.dumps(body.dispatch_packet_json) if body.dispatch_packet_json is not None else None,
                body.title,
                body.objective,
                body.sender,
                t,
            ),
        )
        created_steps = _create_contract_steps(conn, case_id, contract, t)
        _create_contract_slots(conn, case_id, contract)
    return {
        "case_id": case_id,
        "status": "OPEN",
        "created_at": t,
        "contract": contract,
        "steps": created_steps,
        "progress": _build_progress(created_steps),
        "reused": False,
    }


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    request_start = time.perf_counter()
    db_start = time.perf_counter()
    with get_db() as conn:
        _recompute_case_state(conn, case_id)
        row = _load_case_row(conn, case_id)
        steps = conn.execute("SELECT * FROM case_steps WHERE case_id = ? ORDER BY idx", (case_id,)).fetchall()
        slots = conn.execute("SELECT * FROM case_slots WHERE case_id = ? ORDER BY name", (case_id,)).fetchall()
        logs = conn.execute("SELECT * FROM case_logs WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()
        artifacts = conn.execute("SELECT * FROM execution_artifacts WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()
        audits = _audit_rows(conn, case_id)
        contract = _parse_contract(row)
        dispatch_packet = _parse_dispatch_packet(row)
    db_ms = _elapsed_ms(db_start)
    step_dicts = [_step_row_to_dict(step) for step in steps]
    case_dict = dict(row)
    case_dict["dispatch_packet_json"] = dispatch_packet
    payload = {
        "case": case_dict,
        "contract": contract,
        "steps": step_dicts,
        "slots": [dict(s) for s in slots],
        "logs": [dict(l) for l in logs],
        "artifacts": [_row_with_json(a, "metadata_json") for a in artifacts],
        "model_task_audits": audits,
        "progress": _build_progress(step_dicts),
    }
    log.info(
        "case_detail_built",
        extra={
            "case_id": case_id,
            "elapsed_ms": _elapsed_ms(request_start),
            "db_ms": db_ms,
            "step_count": len(step_dicts),
            "slot_count": len(slots),
            "log_count": len(logs),
            "artifact_count": len(artifacts),
            "audit_count": len(audits),
            "response_size_bytes": _safe_json_size(payload),
        },
    )
    return payload


@app.post("/cases/{case_id}/runs", status_code=201)
def create_case_run(case_id: str, body: CreateCaseRunIn):
    request_start = time.perf_counter()
    t = now()
    status = _normalize_observability_status(body.status, CASE_RUN_STATUSES, field="case run status")
    metadata = body.metadata or {}
    idempotency_key = str(body.idempotency_key or "").strip() or None
    reused = False
    db_start = time.perf_counter()
    with get_db() as conn:
        _load_case_row(conn, case_id)
        if idempotency_key:
            existing = conn.execute(
                "SELECT * FROM case_runs WHERE case_id = ? AND idempotency_key = ?",
                (case_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                payload = _row_with_json(existing, "metadata_json")
                payload["reused"] = True
                log.info(
                    "case_run_reused",
                    extra={
                        "case_id": case_id,
                        "case_run_id": payload.get("id"),
                        "idempotency_key": idempotency_key,
                        "elapsed_ms": _elapsed_ms(request_start),
                        "db_ms": _elapsed_ms(db_start),
                    },
                )
                return payload
        run_id = new_id("case_run")
        ended_at = t if status in {"completed", "blocked", "failed", "cancelled"} else None
        started_at = t if status == "running" else None
        conn.execute(
            "INSERT INTO case_runs (id, case_id, runtime_mode, runner, status, idempotency_key, metadata_json, created_at, started_at, updated_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                case_id,
                body.runtime_mode,
                body.runner,
                status,
                idempotency_key,
                json.dumps(metadata, sort_keys=True),
                t,
                started_at,
                t,
                ended_at,
            ),
        )
    db_ms = _elapsed_ms(db_start)
    broadcast_start = time.perf_counter()
    _broadcast(case_id, f"case_run.created:{run_id}")
    _broadcast(f"case_run:{run_id}", f"case_run.created:{run_id}")
    broadcast_ms = _elapsed_ms(broadcast_start)
    payload = {
        "id": run_id,
        "case_id": case_id,
        "runtime_mode": body.runtime_mode,
        "runner": body.runner,
        "status": status,
        "idempotency_key": idempotency_key,
        "metadata_json": metadata,
        "created_at": t,
        "started_at": started_at,
        "updated_at": t,
        "ended_at": ended_at,
        "reused": reused,
    }
    log.info(
        "case_run_created",
        extra={
            "case_id": case_id,
            "case_run_id": run_id,
            "idempotency_key": idempotency_key,
            "elapsed_ms": _elapsed_ms(request_start),
            "db_ms": db_ms,
            "broadcast_ms": broadcast_ms,
            "response_size_bytes": _safe_json_size(payload),
        },
    )
    return payload


@app.get("/cases/{case_id}/runs")
def list_case_runs(case_id: str):
    with get_db() as conn:
        _load_case_row(conn, case_id)
        rows = conn.execute("SELECT * FROM case_runs WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()
    return {"case_runs": [_row_with_json(row, "metadata_json") for row in rows]}


@app.get("/case-runs/{run_id}")
def get_case_run(run_id: str):
    with get_db() as conn:
        row = _case_run_row(conn, run_id)
    return _row_with_json(row, "metadata_json")


@app.put("/case-runs/{run_id}")
def update_case_run(run_id: str, body: UpdateCaseRunIn):
    t = now()
    with get_db() as conn:
        row = _case_run_row(conn, run_id)
        status = row["status"]
        if body.status is not None:
            status = _normalize_observability_status(body.status, CASE_RUN_STATUSES, field="case run status")
        metadata = _merge_metadata(row["metadata_json"], body.metadata)
        started_at = row["started_at"] or (t if status == "running" else None)
        ended_at = row["ended_at"] or (t if status in {"completed", "blocked", "failed", "cancelled"} else None)
        conn.execute(
            "UPDATE case_runs SET status = ?, metadata_json = ?, started_at = ?, updated_at = ?, ended_at = ? WHERE id = ?",
            (status, json.dumps(metadata, sort_keys=True), started_at, t, ended_at, run_id),
        )
        case_id = row["case_id"]
    _broadcast(case_id, f"case_run.updated:{run_id}:{status}")
    _broadcast(f"case_run:{run_id}", f"case_run.updated:{run_id}:{status}")
    return {"ok": True, "id": run_id, "status": status, "updated_at": t}


@app.post("/case-runs/{run_id}/steps", status_code=201)
def create_step_run(run_id: str, body: CreateStepRunIn):
    t = now()
    status = _normalize_observability_status(body.status, STEP_RUN_STATUSES, field="step run status")
    metadata = body.metadata or {}
    idempotency_key = str(body.idempotency_key or "").strip() or None
    with get_db() as conn:
        case_run = _case_run_row(conn, run_id)
        if body.case_run_id != run_id:
            raise HTTPException(422, "case_run_id must match URL run id")
        case_id = case_run["case_id"]
        step_row = conn.execute(
            "SELECT * FROM case_steps WHERE id = ? AND case_id = ?",
            (body.step_db_row_id, case_id),
        ).fetchone()
        if not step_row:
            raise HTTPException(404, "case step not found")
        if step_row["step_id"] != body.step_id:
            raise HTTPException(422, "step_id does not match step_db_row_id")
        if idempotency_key:
            existing = conn.execute(
                "SELECT * FROM step_runs WHERE case_run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                payload = _row_with_json(existing, "metadata_json")
                payload["reused"] = True
                return payload
        step_run_id = new_id("step_run")
        started_at = t if status == "running" else None
        ended_at = t if status in {"completed", "blocked", "failed", "skipped"} else None
        conn.execute(
            "INSERT INTO step_runs (id, case_run_id, case_id, step_id, step_db_row_id, idx, title, executor_type, status, idempotency_key, metadata_json, created_at, started_at, updated_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                step_run_id,
                run_id,
                case_id,
                body.step_id,
                body.step_db_row_id,
                body.idx if body.idx is not None else step_row["idx"],
                body.title or step_row["name"],
                body.executor_type,
                status,
                idempotency_key,
                json.dumps(metadata, sort_keys=True),
                t,
                started_at,
                t,
                ended_at,
            ),
        )
    _broadcast(case_id, f"step_run.created:{step_run_id}")
    _broadcast(f"case_run:{run_id}", f"step_run.created:{step_run_id}")
    return {
        "id": step_run_id,
        "case_run_id": run_id,
        "case_id": case_id,
        "step_id": body.step_id,
        "step_db_row_id": body.step_db_row_id,
        "idx": body.idx if body.idx is not None else step_row["idx"],
        "title": body.title or step_row["name"],
        "executor_type": body.executor_type,
        "status": status,
        "idempotency_key": idempotency_key,
        "metadata_json": metadata,
        "created_at": t,
        "started_at": started_at,
        "updated_at": t,
        "ended_at": ended_at,
        "reused": False,
    }


@app.get("/case-runs/{run_id}/steps")
def list_case_run_steps(run_id: str):
    with get_db() as conn:
        _case_run_row(conn, run_id)
        rows = conn.execute("SELECT * FROM step_runs WHERE case_run_id = ? ORDER BY idx, created_at", (run_id,)).fetchall()
    return {"step_runs": [_row_with_json(row, "metadata_json") for row in rows]}


@app.put("/step-runs/{step_run_id}")
def update_step_run(step_run_id: str, body: UpdateStepRunIn):
    t = now()
    with get_db() as conn:
        row = _step_run_row(conn, step_run_id)
        status = row["status"]
        if body.status is not None:
            status = _normalize_observability_status(body.status, STEP_RUN_STATUSES, field="step run status")
        metadata = _merge_metadata(row["metadata_json"], body.metadata)
        started_at = row["started_at"] or (t if status == "running" else None)
        ended_at = row["ended_at"] or (t if status in {"completed", "blocked", "failed", "skipped"} else None)
        conn.execute(
            "UPDATE step_runs SET status = ?, metadata_json = ?, started_at = ?, updated_at = ?, ended_at = ? WHERE id = ?",
            (status, json.dumps(metadata, sort_keys=True), started_at, t, ended_at, step_run_id),
        )
        case_id = row["case_id"]
        case_run_id = row["case_run_id"]
    _broadcast(case_id, f"step_run.updated:{step_run_id}:{status}")
    _broadcast(f"case_run:{case_run_id}", f"step_run.updated:{step_run_id}:{status}")
    return {"ok": True, "id": step_run_id, "status": status, "updated_at": t}


@app.post("/case-runs/{run_id}/spans", status_code=201)
def create_execution_span(run_id: str, body: CreateExecutionSpanIn):
    t = now()
    status = _normalize_observability_status(body.status, SPAN_STATUSES, field="span status")
    with get_db() as conn:
        case_run = _case_run_row(conn, run_id)
        if body.case_run_id != run_id:
            raise HTTPException(422, "case_run_id must match URL run id")
        if body.step_run_id:
            step_run = _step_run_row(conn, body.step_run_id)
            if step_run["case_run_id"] != run_id:
                raise HTTPException(422, "step_run_id does not belong to case_run_id")
        if body.parent_span_id:
            parent = conn.execute("SELECT * FROM execution_spans WHERE id = ?", (body.parent_span_id,)).fetchone()
            if not parent or parent["case_run_id"] != run_id:
                raise HTTPException(422, "parent_span_id does not belong to case_run_id")
        span_id = new_id("span")
        ended_at = t if status in {"completed", "blocked", "failed"} else None
        conn.execute(
            "INSERT INTO execution_spans (id, case_run_id, step_run_id, parent_span_id, name, status, metadata_json, created_at, started_at, updated_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                span_id,
                run_id,
                body.step_run_id,
                body.parent_span_id,
                body.name,
                status,
                json.dumps(body.metadata or {}, sort_keys=True),
                t,
                t,
                t,
                ended_at,
            ),
        )
        case_id = case_run["case_id"]
    _broadcast(case_id, f"span.created:{span_id}")
    _broadcast(f"case_run:{run_id}", f"span.created:{span_id}")
    return {
        "id": span_id,
        "case_run_id": run_id,
        "step_run_id": body.step_run_id,
        "parent_span_id": body.parent_span_id,
        "name": body.name,
        "status": status,
        "metadata_json": body.metadata or {},
        "created_at": t,
        "started_at": t,
        "updated_at": t,
        "ended_at": ended_at,
    }


@app.put("/execution-spans/{span_id}")
def update_execution_span(span_id: str, body: UpdateExecutionSpanIn):
    t = now()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM execution_spans WHERE id = ?", (span_id,)).fetchone()
        if not row:
            raise HTTPException(404, "execution span not found")
        status = row["status"]
        if body.status is not None:
            status = _normalize_observability_status(body.status, SPAN_STATUSES, field="span status")
        metadata = _merge_metadata(row["metadata_json"], body.metadata)
        ended_at = row["ended_at"] or (t if status in {"completed", "blocked", "failed"} else None)
        conn.execute(
            "UPDATE execution_spans SET status = ?, metadata_json = ?, updated_at = ?, ended_at = ? WHERE id = ?",
            (status, json.dumps(metadata, sort_keys=True), t, ended_at, span_id),
        )
        case_run = _case_run_row(conn, row["case_run_id"])
        case_id = case_run["case_id"]
    _broadcast(case_id, f"span.updated:{span_id}:{status}")
    _broadcast(f"case_run:{row['case_run_id']}", f"span.updated:{span_id}:{status}")
    return {"ok": True, "id": span_id, "status": status, "updated_at": t}


@app.get("/step-runs/{step_run_id}/spans")
def list_step_run_spans(step_run_id: str):
    with get_db() as conn:
        _step_run_row(conn, step_run_id)
        rows = conn.execute("SELECT * FROM execution_spans WHERE step_run_id = ? ORDER BY created_at", (step_run_id,)).fetchall()
    return {"spans": [_row_with_json(row, "metadata_json") for row in rows]}


@app.post("/case-runs/{run_id}/events", status_code=201)
def create_execution_event(run_id: str, body: CreateExecutionEventIn):
    t = now()
    severity = _normalize_observability_status(body.severity, EVENT_SEVERITIES, field="event severity")
    event_type = str(body.type or "").strip()
    if not event_type:
        raise HTTPException(422, "event type is required")
    with get_db() as conn:
        case_run = _case_run_row(conn, run_id)
        if body.case_run_id != run_id:
            raise HTTPException(422, "case_run_id must match URL run id")
        if body.step_run_id:
            step_run = _step_run_row(conn, body.step_run_id)
            if step_run["case_run_id"] != run_id:
                raise HTTPException(422, "step_run_id does not belong to case_run_id")
        event_id = new_id("event")
        conn.execute(
            "INSERT INTO execution_events (id, case_id, case_run_id, step_run_id, span_id, type, severity, message, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                case_run["case_id"],
                run_id,
                body.step_run_id,
                body.span_id,
                event_type,
                severity,
                body.message,
                json.dumps(body.metadata or {}, sort_keys=True),
                t,
            ),
        )
    _broadcast(case_run["case_id"], f"execution_event.created:{event_id}:{event_type}")
    _broadcast(f"case_run:{run_id}", f"execution_event.created:{event_id}:{event_type}")
    return {
        "id": event_id,
        "case_id": case_run["case_id"],
        "case_run_id": run_id,
        "step_run_id": body.step_run_id,
        "span_id": body.span_id,
        "type": event_type,
        "severity": severity,
        "message": body.message,
        "metadata_json": body.metadata or {},
        "created_at": t,
    }


@app.get("/case-runs/{run_id}/events")
def list_case_run_events(run_id: str, event_type: str | None = None, span_id: str | None = None):
    with get_db() as conn:
        _case_run_row(conn, run_id)
        query = "SELECT * FROM execution_events WHERE case_run_id = ?"
        params: list[Any] = [run_id]
        if event_type:
            query += " AND type = ?"
            params.append(event_type)
        if span_id:
            query += " AND span_id = ?"
            params.append(span_id)
        query += " ORDER BY created_at"
        rows = conn.execute(query, params).fetchall()
    return {"events": [_row_with_json(row, "metadata_json") for row in rows]}


@app.get("/step-runs/{step_run_id}/events")
def list_step_run_events(step_run_id: str, event_type: str | None = None, span_id: str | None = None):
    with get_db() as conn:
        _step_run_row(conn, step_run_id)
        query = "SELECT * FROM execution_events WHERE step_run_id = ?"
        params: list[Any] = [step_run_id]
        if event_type:
            query += " AND type = ?"
            params.append(event_type)
        if span_id:
            query += " AND span_id = ?"
            params.append(span_id)
        query += " ORDER BY created_at"
        rows = conn.execute(query, params).fetchall()
    return {"events": [_row_with_json(row, "metadata_json") for row in rows]}


@app.post("/case-runs/{run_id}/artifacts", status_code=201)
def create_execution_artifact(run_id: str, body: CreateExecutionArtifactIn):
    t = now()
    role = str(body.role or "").strip()
    uri = str(body.uri or "").strip()
    if not role:
        raise HTTPException(422, "artifact role is required")
    if not uri:
        raise HTTPException(422, "artifact uri is required")
    with get_db() as conn:
        case_run = _case_run_row(conn, run_id)
        if body.case_run_id != run_id:
            raise HTTPException(422, "case_run_id must match URL run id")
        if body.step_run_id:
            step_run = _step_run_row(conn, body.step_run_id)
            if step_run["case_run_id"] != run_id:
                raise HTTPException(422, "step_run_id does not belong to case_run_id")
        artifact_id = new_id("artifact")
        conn.execute(
            "INSERT INTO execution_artifacts (id, case_id, case_run_id, step_run_id, span_id, role, uri, sha256, size_bytes, content_type, redaction_status, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact_id,
                case_run["case_id"],
                run_id,
                body.step_run_id,
                body.span_id,
                role,
                uri,
                body.sha256,
                body.size_bytes,
                body.content_type,
                body.redaction_status,
                json.dumps(body.metadata or {}, sort_keys=True),
                t,
            ),
        )
    _broadcast(case_run["case_id"], f"execution_artifact.created:{artifact_id}:{role}")
    _broadcast(f"case_run:{run_id}", f"execution_artifact.created:{artifact_id}:{role}")
    return {
        "id": artifact_id,
        "case_id": case_run["case_id"],
        "case_run_id": run_id,
        "step_run_id": body.step_run_id,
        "span_id": body.span_id,
        "role": role,
        "uri": uri,
        "sha256": body.sha256,
        "size_bytes": body.size_bytes,
        "content_type": body.content_type,
        "redaction_status": body.redaction_status,
        "metadata_json": body.metadata or {},
        "created_at": t,
    }


@app.get("/step-runs/{step_run_id}/artifacts")
def list_step_run_artifacts(step_run_id: str):
    with get_db() as conn:
        _step_run_row(conn, step_run_id)
        rows = conn.execute("SELECT * FROM execution_artifacts WHERE step_run_id = ? ORDER BY created_at", (step_run_id,)).fetchall()
    return {"artifacts": [_row_with_json(row, "metadata_json") for row in rows]}


@app.get("/mirror/files/{encoded_path}/content")
def get_mirror_file_content(encoded_path: str):
    path = _mirror_filesystem_path(_decode_mirror_path(encoded_path))
    guessed, _ = mimetypes.guess_type(str(path))
    return FileResponse(
        path,
        media_type=guessed or "application/octet-stream",
        filename=path.name,
        headers={
            "X-Hub-Mirror-Path": str(path),
            "Cache-Control": "private, max-age=60",
        },
    )


@app.get("/case-runs/{run_id}/artifacts")
def list_case_run_artifacts(run_id: str):
    with get_db() as conn:
        _case_run_row(conn, run_id)
        rows = conn.execute("SELECT * FROM execution_artifacts WHERE case_run_id = ? ORDER BY created_at", (run_id,)).fetchall()
    return {"artifacts": [_row_with_json(row, "metadata_json") for row in rows]}


@app.get("/execution-artifacts/{artifact_id}")
def get_execution_artifact(artifact_id: str):
    with get_db() as conn:
        row = _artifact_row(conn, artifact_id)
        return _row_with_json(row, "metadata_json")


@app.get("/execution-artifacts/{artifact_id}/content")
def get_execution_artifact_content(artifact_id: str):
    with get_db() as conn:
        row = _artifact_row(conn, artifact_id)
        if str(row["redaction_status"] or "").lower() in {"redacted", "restricted"}:
            raise HTTPException(403, "artifact content is redacted or restricted")
        path = _artifact_filesystem_path(row)
        content_type = _artifact_content_type(row, path)
    return FileResponse(
        path,
        media_type=content_type,
        filename=path.name,
        headers={
            "X-Zenith-Artifact-Id": artifact_id,
            "X-Zenith-Artifact-Role": str(row["role"] or ""),
        },
    )


@app.get("/case-runs/{run_id}/artifacts/{artifact_id}")
def get_case_run_artifact(run_id: str, artifact_id: str):
    with get_db() as conn:
        _case_run_row(conn, run_id)
        row = _artifact_row(conn, artifact_id)
        if row["case_run_id"] != run_id:
            raise HTTPException(404, "artifact not found for case run")
        return _row_with_json(row, "metadata_json")


@app.get("/case-runs/{run_id}/artifacts/{artifact_id}/content")
def get_case_run_artifact_content(run_id: str, artifact_id: str):
    with get_db() as conn:
        _case_run_row(conn, run_id)
        row = _artifact_row(conn, artifact_id)
        if row["case_run_id"] != run_id:
            raise HTTPException(404, "artifact not found for case run")
        if str(row["redaction_status"] or "").lower() in {"redacted", "restricted"}:
            raise HTTPException(403, "artifact content is redacted or restricted")
        path = _artifact_filesystem_path(row)
        content_type = _artifact_content_type(row, path)
    return FileResponse(
        path,
        media_type=content_type,
        filename=path.name,
        headers={
            "X-Zenith-Artifact-Id": artifact_id,
            "X-Zenith-Artifact-Role": str(row["role"] or ""),
        },
    )


@app.get("/case-runs/{run_id}/stream")
async def stream_case_run(run_id: str):
    queue: asyncio.Queue = asyncio.Queue()
    key = f"case_run:{run_id}"
    _subscribers.setdefault(key, []).append(queue)

    async def generator():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: update\ndata: {json.dumps({'event': event, 'case_run_id': run_id})}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subs = _subscribers.get(key, [])
            if queue in subs:
                subs.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/cases/{case_id}/board")
def get_case_board(case_id: str):
    with get_db() as conn:
        case_row = _load_case_row(conn, case_id)
        contract = _parse_contract(case_row) or {}
        slots_by_name = _slot_rows_by_name(conn, case_id)
        variables, variable_counts = _board_variable_projection(
            case_id=case_id,
            contract=contract,
            slots_by_name=slots_by_name,
        )
        steps = [_step_row_to_dict(step) for step in _step_rows(conn, case_id)]
        latest_runs: dict[str, dict[str, Any]] = {}
        rows = conn.execute(
            "SELECT * FROM step_runs WHERE case_id = ? ORDER BY created_at",
            (case_id,),
        ).fetchall()
        for row in rows:
            latest_runs[row["step_db_row_id"]] = _row_with_json(row, "metadata_json")

    columns = [
        {"id": "pending", "title": "Pending", "task_ids": []},
        {"id": "ready", "title": "Ready", "task_ids": []},
        {"id": "running", "title": "Running", "task_ids": []},
        {"id": "completed", "title": "Completed", "task_ids": []},
        {"id": "blocked", "title": "Blocked", "task_ids": []},
        {"id": "failed", "title": "Failed", "task_ids": []},
    ]
    column_by_status = {column["id"]: column for column in columns}
    tasks: list[dict[str, Any]] = []
    for step in steps:
        step_run = latest_runs.get(step["id"])
        status = str((step_run or {}).get("status") or step["status"] or "pending").lower()
        if status == "skipped":
            status = "completed"
        if status not in column_by_status:
            status = "pending"
        task_id = f"case_step:{step['id']}"
        column_by_status[status]["task_ids"].append(task_id)
        tasks.append(
            {
                "id": task_id,
                "case_id": case_id,
                "step_db_row_id": step["id"],
                "step_id": step["step_id"],
                "title": step["name"],
                "status": status,
                "step_status": step["status"],
                "step_run_id": (step_run or {}).get("id"),
                "source": "cases",
            }
        )
    return {
        "case_id": case_id,
        "source": "cases",
        "columns": columns,
        "tasks": tasks,
        "variables": variables,
        "variable_counts": variable_counts,
    }


@app.post("/cases/{case_id}/steps", status_code=201)
def create_steps(case_id: str, body: CreateStepsIn):
    t = now()
    created = []
    with get_db() as conn:
        row = _load_case_row(conn, case_id)
        if row["contract_json"]:
            raise HTTPException(409, "contract-backed cases instantiate steps from contract_json")
        for s in body.steps:
            step_db_id = new_id("step")
            conn.execute(
            "INSERT INTO case_steps (id, case_id, idx, step_id, name, executor, action, args_json, status, runtime_state_json, created_at, updated_at, runtime_updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, ?, ?, NULL, NULL)",
                (
                    step_db_id,
                    case_id,
                    s["idx"],
                    s["step_id"],
                    s["name"],
                    s.get("executor"),
                    s["action"],
                    json.dumps(s.get("args_json", {})),
                    t,
                    t,
                ),
            )
            created.append(step_db_id)
    return {"created": created}


@app.put("/cases/{case_id}/steps/{step_db_id}")
def update_step(case_id: str, step_db_id: str, body: UpdateStepIn):
    t = now()
    with get_db() as conn:
        row = _load_case_row(conn, case_id)
        step_row = conn.execute(
            "SELECT * FROM case_steps WHERE id = ? AND case_id = ?",
            (step_db_id, case_id),
        ).fetchone()
        if not step_row:
            raise HTTPException(404, "step not found")

        contract = _parse_contract(row)
        normalized_status = _normalize_step_status(body.status)
        if contract and body.result_json is not None:
            try:
                step_contract = _contract_step(contract, step_row["step_id"])
            except KeyError as exc:
                _write_case_log(
                    conn,
                    case_id,
                    "error",
                    f"Rejected result write for unknown contract step {step_row['step_id']}",
                    step_id=step_row["step_id"],
                )
                conn.commit()
                raise HTTPException(409, "step is missing from the persisted contract") from exc

            allowed = set(step_contract.get("output_variables", []))
            provided = set(body.result_json.keys())
            extras = sorted(provided - allowed)
            if extras:
                _write_case_log(
                    conn,
                    case_id,
                    "error",
                    f"Rejected undeclared outputs for {step_row['step_id']}: {', '.join(extras)}",
                    step_id=step_row["step_id"],
                    metadata={"extra_keys": extras},
                )
                conn.commit()
                raise HTTPException(422, f"undeclared step outputs: {', '.join(extras)}")

            conflicts: list[str] = []
            idempotent_writes: set[str] = set()
            slots_by_name = _slot_rows_by_name(conn, case_id)
            for name, value in body.result_json.items():
                state = _slot_write_state(slots_by_name.get(name), value)
                if state == "conflict":
                    conflicts.append(name)
                elif state == "idempotent":
                    idempotent_writes.add(name)
            if conflicts:
                _write_case_log(
                    conn,
                    case_id,
                    "error",
                    f"Rejected rewritten outputs for {step_row['step_id']}: {', '.join(conflicts)}",
                    step_id=step_row["step_id"],
                    metadata={"conflicting_outputs": conflicts},
                )
                conn.commit()
                raise HTTPException(409, f"output slots already populated: {', '.join(conflicts)}")

            for name, value in body.result_json.items():
                if name in idempotent_writes:
                    continue
                _write_slot_value_once(
                    conn,
                    case_id,
                    name,
                    value,
                    t,
                    agent_run_id=body.agent_run_id,
                    produced_at=t if body.agent_run_id else None,
                )

        result = json.dumps(body.result_json) if body.result_json is not None else step_row["result_json"]
        if normalized_status is not None:
            completed_at = t if normalized_status == "COMPLETED" else None
            conn.execute(
                "UPDATE case_steps SET status = ?, result_json = ?, updated_at = ?, completed_at = ? WHERE id = ? AND case_id = ?",
                (normalized_status, result, t, completed_at, step_db_id, case_id),
            )
        elif body.result_json is not None:
            conn.execute(
                "UPDATE case_steps SET result_json = ?, updated_at = ? WHERE id = ? AND case_id = ?",
                (result, t, step_db_id, case_id),
            )
        _recompute_case_state(conn, case_id, t=t)
        updated_step = conn.execute(
            "SELECT status FROM case_steps WHERE id = ? AND case_id = ?",
            (step_db_id, case_id),
        ).fetchone()
    _broadcast(case_id, f"step.updated:{step_db_id}:{updated_step['status']}")
    return {"ok": True}


@app.put("/cases/{case_id}/steps/{step_db_id}/runtime-state")
def update_step_runtime_state(case_id: str, step_db_id: str, body: UpdateStepRuntimeStateIn):
    t = now()
    with get_db() as conn:
        _load_case_row(conn, case_id)
        step_row = conn.execute(
            "SELECT * FROM case_steps WHERE id = ? AND case_id = ?",
            (step_db_id, case_id),
        ).fetchone()
        if not step_row:
            raise HTTPException(404, "step not found")
        conn.execute(
            "UPDATE case_steps SET runtime_state_json = ?, runtime_updated_at = ?, updated_at = ? WHERE id = ? AND case_id = ?",
            (json.dumps(body.runtime_state_json), t, t, step_db_id, case_id),
        )
    _broadcast(case_id, f"step.runtime:{step_db_id}")
    return {"ok": True, "runtime_updated_at": t}


@app.post("/cases/{case_id}/steps/{step_db_id}/complete-outputs")
def complete_step_outputs(case_id: str, step_db_id: str, body: CompleteStepOutputsIn):
    t = now()
    with get_db() as conn:
        row = _load_case_row(conn, case_id)
        step_row = conn.execute(
            "SELECT * FROM case_steps WHERE id = ? AND case_id = ?",
            (step_db_id, case_id),
        ).fetchone()
        if not step_row:
            raise HTTPException(404, "step not found")

        contract = _parse_contract(row)
        if not contract:
            raise HTTPException(409, "case is missing persisted contract")
        try:
            step_contract = _contract_step(contract, step_row["step_id"])
        except KeyError as exc:
            raise HTTPException(409, "step is missing from the persisted contract") from exc

        declared_outputs = list(step_contract.get("output_variables") or [])
        if not declared_outputs:
            raise HTTPException(422, "step does not declare output variables")

        provided_keys = set(body.outputs_json.keys())
        expected_keys = set(declared_outputs)
        missing = sorted(expected_keys - provided_keys)
        extras = sorted(provided_keys - expected_keys)
        if missing or extras:
            _write_case_log(
                conn,
                case_id,
                "error",
                f"Rejected invalid output envelope for {step_row['step_id']}",
                step_id=step_row["step_id"],
                metadata={"missing_outputs": missing, "extra_outputs": extras},
            )
            conn.commit()
            raise HTTPException(
                422,
                f"output envelope mismatch; missing={missing or []} extra={extras or []}",
            )

        variables = contract.get("variables") or {}
        type_errors: list[dict[str, str]] = []
        for name in declared_outputs:
            meta = variables.get(name) or {}
            expected_type = str(meta.get("type") or "").strip()
            if expected_type and not _matches_variable_type(expected_type, body.outputs_json.get(name)):
                type_errors.append({"name": name, "expected_type": expected_type})
        if type_errors:
            _write_case_log(
                conn,
                case_id,
                "error",
                f"Rejected typed outputs for {step_row['step_id']}",
                step_id=step_row["step_id"],
                metadata={"type_errors": type_errors},
            )
            conn.commit()
            raise HTTPException(422, f"typed output validation failed: {type_errors}")

        slots_by_name = _slot_rows_by_name(conn, case_id)
        conflicts: list[str] = []
        idempotent_writes: set[str] = set()
        for name, value in body.outputs_json.items():
            state = _slot_write_state(slots_by_name.get(name), value)
            if state == "conflict":
                conflicts.append(name)
            elif state == "idempotent":
                idempotent_writes.add(name)
        if conflicts:
            _write_case_log(
                conn,
                case_id,
                "error",
                f"Rejected rewritten outputs for {step_row['step_id']}: {', '.join(conflicts)}",
                step_id=step_row["step_id"],
                metadata={"conflicting_outputs": conflicts},
            )
            conn.commit()
            raise HTTPException(409, f"output slots already populated: {', '.join(conflicts)}")

        for name, value in body.outputs_json.items():
            if name in idempotent_writes:
                continue
            _write_slot_value_once(
                conn,
                case_id,
                name,
                value,
                t,
                agent_run_id=body.agent_run_id,
                produced_at=t if body.agent_run_id else None,
            )

        runtime_state = _parse_runtime_state(step_row["runtime_state_json"]) or {}
        runtime_state.update(
            {
                "status": "completed",
                "completed_at": t,
                "committed_outputs": declared_outputs,
            }
        )
        conn.execute(
            "UPDATE case_steps SET status = ?, result_json = ?, runtime_state_json = ?, runtime_updated_at = ?, updated_at = ?, completed_at = ? WHERE id = ? AND case_id = ?",
            (
                "COMPLETED",
                json.dumps(body.outputs_json),
                json.dumps(runtime_state),
                t,
                t,
                t,
                step_db_id,
                case_id,
            ),
        )
        for note in body.notes or []:
            if str(note).strip():
                _write_case_log(
                    conn,
                    case_id,
                    "info",
                    str(note).strip(),
                    step_id=step_row["step_id"],
                )
        _recompute_case_state(conn, case_id, t=t)
    _broadcast(case_id, f"step.updated:{step_db_id}:COMPLETED")
    return {"ok": True, "completed_at": t}


@app.post("/cases/{case_id}/slots")
def upsert_slot(case_id: str, body: UpsertSlotIn):
    t = now()
    with get_db() as conn:
        row = _load_case_row(conn, case_id)
        contract = _parse_contract(row)
        if contract and body.name not in set(contract["slot_names"]):
            _write_case_log(
                conn,
                case_id,
                "error",
                f"Rejected undeclared slot write: {body.name}",
                metadata={"slot_name": body.name},
            )
            conn.commit()
            raise HTTPException(422, f"undeclared slot name: {body.name}")

        try:
            state = _write_slot_value_once(
                conn,
                case_id,
                body.name,
                body.value,
                t,
                agent_run_id=body.agent_run_id,
                produced_at=t if body.agent_run_id else None,
            )
        except SlotWriteConflictError as exc:
            _write_case_log(
                conn,
                case_id,
                "error",
                f"Rejected slot rewrite: {body.name}",
                metadata={"slot_name": body.name},
            )
            conn.commit()
            raise HTTPException(409, str(exc)) from exc
        _recompute_case_state(conn, case_id, t=t)
    _broadcast(case_id, f"slot.filled:{body.name}")
    return {"ok": True, "idempotent": state == "idempotent"}


@app.put("/cases/{case_id}/dispatch-packet")
def update_dispatch_packet(case_id: str, body: UpdateDispatchPacketIn):
    with get_db() as conn:
        _load_case_row(conn, case_id)
        conn.execute(
            "UPDATE cases SET dispatch_packet_json = ? WHERE id = ?",
            (json.dumps(body.dispatch_packet_json), case_id),
        )
    _broadcast(case_id, "case.dispatch_packet")
    return {"ok": True}



@app.post("/cases/{case_id}/model-task-audits", status_code=201)
def upsert_model_task_audit(case_id: str, body: UpsertModelTaskAuditIn):
    t = now()
    try:
        record = dict(body.audit_record or {})
        _reject_secret_like_fields(record)
        _validate_audit_identity(case_id, body.step_id, record)
        stable_key = _audit_stable_key(case_id, body.step_id, record)
        safe_record = _safe_audit_record(record)
        reference = _audit_reference(case_id, body.step_id, stable_key, safe_record, status="created")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    with get_db() as conn:
        _load_case_row(conn, case_id)
        existing = conn.execute(
            "SELECT * FROM model_task_audits WHERE case_id = ? AND idempotency_key = ?",
            (case_id, stable_key),
        ).fetchone()
        if existing is not None:
            existing_reference = json.loads(existing["reference_json"])
            try:
                _reject_audit_collision(existing_reference, reference)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            reused = dict(existing_reference)
            reused["upsert_status"] = "reused"
            return reused

        conn.execute(
            "INSERT INTO model_task_audits (id, case_id, step_id, step_db_row_id, idempotency_key, reference_json, audit_record_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reference["audit_record_id"],
                case_id,
                body.step_id,
                record.get("step_db_row_id"),
                stable_key,
                json.dumps(reference, sort_keys=True),
                json.dumps(safe_record, sort_keys=True),
                t,
                t,
            ),
        )
        _write_case_log(
            conn,
            case_id,
            "model_task_audit",
            "canonical model-task audit persisted",
            step_id=body.step_id,
            metadata={"audit_record_id": reference["audit_record_id"], "idempotency_key": stable_key},
        )
    _broadcast(case_id, f"model_task_audit.upserted:{reference['audit_record_id']}")
    return reference


@app.post("/cases/{case_id}/logs", status_code=201)
def create_log(case_id: str, body: CreateLogIn):
    log_id = new_id("log")
    t = now()
    with get_db() as conn:
        _load_case_row(conn, case_id)
        conn.execute(
            "INSERT INTO case_logs (id, case_id, step_id, type, message, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (log_id, case_id, body.step_id, body.type, body.message, json.dumps(body.metadata or {}), t),
        )
    _broadcast(case_id, f"log.added:{log_id}")
    return {"log_id": log_id}


@app.put("/cases/{case_id}/status")
def update_case_status(case_id: str, body: UpdateStatusIn):
    t = now()
    status = _normalize_case_status(body.status)
    completed_at = t if status in TERMINAL_CASE_STATUSES else None
    with get_db() as conn:
        row = _load_case_row(conn, case_id)
        claimed_at = row["claimed_at"]
        if status == "IN_PROGRESS" and not claimed_at:
            claimed_at = t
        conn.execute(
            "UPDATE cases SET status = ?, claimed_at = ?, completed_at = ? WHERE id = ?",
            (status, claimed_at, completed_at, case_id),
        )
    _broadcast(case_id, f"case.status:{status}")
    return {"ok": True}


@app.get("/cases/{case_id}/stream")
async def stream_case(case_id: str):
    """SSE stream — pushes an event whenever this case's steps, slots, logs, or status change."""
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(case_id, []).append(queue)

    async def generator():
        try:
            # Send an initial ping so the client knows the connection is live
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: update\ndata: {json.dumps({'event': event, 'case_id': case_id})}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            subs = _subscribers.get(case_id, [])
            if queue in subs:
                subs.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


CASE_LIST_LIGHT_COLUMNS = (
    "id, queue_message_id, process_name, process_path, process_hash, title, objective, "
    "sender, status, created_at, claimed_at, completed_at"
)


@app.get("/cases")
def list_cases(
    status: str | None = None,
    sender: str | None = None,
    limit: int = 50,
    include_heavy: bool = False,
):
    request_start = time.perf_counter()
    with get_db() as conn:
        columns = "*" if include_heavy else CASE_LIST_LIGHT_COLUMNS
        q = f"SELECT {columns} FROM cases WHERE 1=1"
        params: list[Any] = []
        if status:
            status = _normalize_case_status(status)
            q += " AND status = ?"
            params.append(status)
        if sender:
            q += " AND sender = ?"
            params.append(sender)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
    cases = [dict(r) for r in rows]
    payload = {"cases": cases}
    log.info(
        "case_list_built",
        extra={
            "status_filter": status,
            "sender_filter_present": bool(sender),
            "limit": limit,
            "include_heavy": include_heavy,
            "case_count": len(cases),
            "elapsed_ms": _elapsed_ms(request_start),
            "response_size_bytes": _safe_json_size(payload),
            "includes_heavy_fields": any(
                any(field in case for field in ("process_source", "contract_json", "dispatch_packet_json"))
                for case in cases
            ),
        },
    )
    return payload


app_instance = app
