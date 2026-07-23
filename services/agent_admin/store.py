from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .domain import (
    DesiredState,
    EvidenceRecord,
    LifecycleAction,
    ObservedState,
    OperationRecord,
    OperationState,
    ProfileRecord,
)


class StoreError(RuntimeError):
    pass


class ProfileNotFound(StoreError):
    pass


class RevisionConflict(StoreError):
    pass


class InvalidTransition(StoreError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dump_time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _load_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class AgentAdminStore:
    def __init__(self, path: Path, *, configured_profile_id: str) -> None:
        self._configured_profile_id = configured_profile_id
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = DELETE")
            self._conn.execute("PRAGMA synchronous = FULL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    profile_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    desired_state INTEGER NOT NULL,
                    observed_state INTEGER NOT NULL,
                    observed_reason_code TEXT NOT NULL DEFAULT '',
                    matrix_secret_arn TEXT NOT NULL DEFAULT '',
                    matrix_credential_active INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    last_observed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
                    action INTEGER NOT NULL,
                    state INTEGER NOT NULL,
                    provider_operation_ref TEXT NOT NULL DEFAULT '',
                    reason_code TEXT NOT NULL DEFAULT '',
                    requested_at TEXT NOT NULL,
                    completed_at TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    expected_revision INTEGER NOT NULL CHECK (expected_revision > 0)
                );

                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
                    operation_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    digest_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                    observed_at TEXT NOT NULL
                );
                """
            )
            operation_columns = {
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(operations)").fetchall()
            }
            if "expected_revision" not in operation_columns:
                self._conn.execute(
                    "ALTER TABLE operations ADD COLUMN expected_revision INTEGER NOT NULL DEFAULT 0"
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _require_configured_profile(self, profile_id: str) -> None:
        if profile_id != self._configured_profile_id:
            raise ValueError("profile_id does not match the configured profile")

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> ProfileRecord:
        return ProfileRecord(
            profile_id=str(row["profile_id"]),
            revision=int(row["revision"]),
            desired_state=DesiredState(int(row["desired_state"])),
            observed_state=ObservedState(int(row["observed_state"])),
            observed_reason_code=str(row["observed_reason_code"]),
            matrix_secret_arn=str(row["matrix_secret_arn"]),
            matrix_credential_active=bool(row["matrix_credential_active"]),
            updated_at=_load_time(str(row["updated_at"])) or _now(),
            last_observed_at=_load_time(row["last_observed_at"]),
        )

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> OperationRecord:
        return OperationRecord(
            operation_id=str(row["operation_id"]),
            profile_id=str(row["profile_id"]),
            action=LifecycleAction(int(row["action"])),
            state=OperationState(int(row["state"])),
            provider_operation_ref=str(row["provider_operation_ref"]),
            reason_code=str(row["reason_code"]),
            requested_at=_load_time(str(row["requested_at"])) or _now(),
            completed_at=_load_time(row["completed_at"]),
        )

    def register_profile(self, profile_id: str) -> ProfileRecord:
        self._require_configured_profile(profile_id)
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT * FROM profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
            if existing is not None:
                return self._profile_from_row(existing)
            now = _now()
            self._conn.execute(
                """
                INSERT INTO profiles (
                    profile_id, revision, desired_state, observed_state, updated_at
                ) VALUES (?, 1, ?, ?, ?)
                """,
                (
                    profile_id,
                    int(DesiredState.DISABLED),
                    int(ObservedState.UNKNOWN),
                    _dump_time(now),
                ),
            )
            return self.get_profile(profile_id)

    def get_profile(self, profile_id: str) -> ProfileRecord:
        self._require_configured_profile(profile_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            raise ProfileNotFound("profile is not registered")
        return self._profile_from_row(row)

    def _require_revision(self, profile_id: str, expected_revision: int) -> ProfileRecord:
        profile = self.get_profile(profile_id)
        if profile.revision != expected_revision:
            raise RevisionConflict("profile revision does not match")
        return profile

    def set_desired_state(
        self,
        profile_id: str,
        *,
        desired_state: DesiredState,
        expected_revision: int,
    ) -> ProfileRecord:
        if desired_state not in {DesiredState.ENABLED, DesiredState.DISABLED}:
            raise ValueError("desired state must be enabled or disabled")
        with self._lock, self._conn:
            self._require_revision(profile_id, expected_revision)
            cursor = self._conn.execute(
                """
                UPDATE profiles
                   SET desired_state = ?, revision = revision + 1, updated_at = ?
                 WHERE profile_id = ? AND revision = ?
                """,
                (int(desired_state), _dump_time(_now()), profile_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("profile revision does not match")
            return self.get_profile(profile_id)

    def set_matrix_credential_reference(
        self,
        profile_id: str,
        *,
        secret_arn: str,
        expected_revision: int,
    ) -> ProfileRecord:
        if not secret_arn.startswith("arn:") or "\n" in secret_arn or "\r" in secret_arn:
            raise ValueError("invalid secret ARN")
        with self._lock, self._conn:
            self._require_revision(profile_id, expected_revision)
            cursor = self._conn.execute(
                """
                UPDATE profiles
                   SET matrix_secret_arn = ?, matrix_credential_active = 1,
                       revision = revision + 1, updated_at = ?
                 WHERE profile_id = ? AND revision = ?
                """,
                (secret_arn, _dump_time(_now()), profile_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("profile revision does not match")
            return self.get_profile(profile_id)

    def deactivate_matrix_credential_reference(
        self,
        profile_id: str,
        *,
        expected_revision: int,
    ) -> ProfileRecord:
        with self._lock, self._conn:
            profile = self._require_revision(profile_id, expected_revision)
            if profile.desired_state is not DesiredState.DISABLED:
                raise InvalidTransition("profile must be disabled before credential deactivation")
            cursor = self._conn.execute(
                """
                UPDATE profiles
                   SET matrix_secret_arn = '', matrix_credential_active = 0,
                       revision = revision + 1, updated_at = ?
                 WHERE profile_id = ? AND revision = ?
                """,
                (_dump_time(_now()), profile_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("profile revision does not match")
            return self.get_profile(profile_id)

    def create_operation(
        self,
        profile_id: str,
        *,
        action: LifecycleAction,
        expected_revision: int,
        idempotency_key: str,
    ) -> tuple[OperationRecord, bool]:
        if action is LifecycleAction.UNSPECIFIED:
            raise ValueError("lifecycle action is required")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency key must be 1-128 characters")
        with self._lock, self._conn:
            self._require_configured_profile(profile_id)
            existing = self._conn.execute(
                "SELECT * FROM operations WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                operation = self._operation_from_row(existing)
                if (
                    operation.profile_id != profile_id
                    or operation.action is not action
                    or int(existing["expected_revision"]) != expected_revision
                ):
                    raise InvalidTransition("idempotency key belongs to another operation")
                return operation, False
            self._require_revision(profile_id, expected_revision)
            operation_id = str(uuid.uuid4())
            now = _now()
            self._conn.execute(
                """
                INSERT INTO operations (
                    operation_id, profile_id, action, state, requested_at,
                    idempotency_key, expected_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    profile_id,
                    int(action),
                    int(OperationState.PENDING),
                    _dump_time(now),
                    idempotency_key,
                    expected_revision,
                ),
            )
            return self.get_operation(profile_id, operation_id), True

    def get_operation(self, profile_id: str, operation_id: str) -> OperationRecord:
        self._require_configured_profile(profile_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE profile_id = ? AND operation_id = ?",
                (profile_id, operation_id),
            ).fetchone()
        if row is None:
            raise ProfileNotFound("operation was not found")
        return self._operation_from_row(row)

    def get_operation_by_idempotency(
        self,
        profile_id: str,
        *,
        action: LifecycleAction,
        expected_revision: int,
        idempotency_key: str,
    ) -> OperationRecord | None:
        self._require_configured_profile(profile_id)
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency key must be 1-128 characters")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        if row is None:
            return None
        operation = self._operation_from_row(row)
        if (
            operation.profile_id != profile_id
            or operation.action is not action
            or int(row["expected_revision"]) != expected_revision
        ):
            raise InvalidTransition("idempotency key belongs to another operation")
        return operation

    def update_operation(
        self,
        operation_id: str,
        *,
        state: OperationState,
        provider_operation_ref: str,
        reason_code: str,
    ) -> OperationRecord:
        if state not in {
            OperationState.DISPATCHED,
            OperationState.SUCCEEDED,
            OperationState.FAILED,
        }:
            raise ValueError("invalid operation state update")
        if "\n" in provider_operation_ref or "\r" in provider_operation_ref:
            raise ValueError("invalid provider operation reference")
        if not reason_code.replace("_", "").isalnum() and reason_code:
            raise ValueError("reason code must be stable and machine-readable")
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise ProfileNotFound("operation was not found")
            current = self._operation_from_row(row)
            if current.state in {OperationState.SUCCEEDED, OperationState.FAILED}:
                if (
                    current.state is state
                    and current.provider_operation_ref == provider_operation_ref
                    and current.reason_code == reason_code
                ):
                    return current
                raise InvalidTransition("operation is already terminal")
            completed_at = _now() if state in {OperationState.SUCCEEDED, OperationState.FAILED} else None
            self._conn.execute(
                """
                UPDATE operations
                   SET state = ?, provider_operation_ref = ?, reason_code = ?, completed_at = ?
                 WHERE operation_id = ?
                """,
                (
                    int(state),
                    provider_operation_ref,
                    reason_code,
                    _dump_time(completed_at),
                    operation_id,
                ),
            )
            return self.get_operation(current.profile_id, operation_id)

    def finalize_operation(
        self,
        operation_id: str,
        *,
        state: OperationState,
        provider_operation_ref: str,
        reason_code: str,
        observed_state: ObservedState | None,
        digest_sha256: str,
        size_bytes: int,
    ) -> OperationRecord:
        if state not in {OperationState.SUCCEEDED, OperationState.FAILED}:
            raise ValueError("final operation state is required")
        if "\n" in provider_operation_ref or "\r" in provider_operation_ref:
            raise ValueError("invalid provider operation reference")
        if reason_code and not reason_code.replace("_", "").isalnum():
            raise ValueError("reason code must be stable and machine-readable")
        if observed_state is ObservedState.UNSPECIFIED:
            raise ValueError("invalid observed state")
        if len(digest_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in digest_sha256
        ):
            raise ValueError("evidence digest must be lowercase SHA-256")
        if size_bytes < 0:
            raise ValueError("evidence size cannot be negative")

        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise ProfileNotFound("operation was not found")
            current = self._operation_from_row(row)
            if current.state in {OperationState.SUCCEEDED, OperationState.FAILED}:
                if (
                    current.state is not state
                    or current.provider_operation_ref != provider_operation_ref
                    or current.reason_code != reason_code
                ):
                    raise InvalidTransition("operation is already terminal")
                completed_at = current.completed_at or _now()
            else:
                completed_at = _now()
                self._conn.execute(
                    """
                    UPDATE operations
                       SET state = ?, provider_operation_ref = ?, reason_code = ?, completed_at = ?
                     WHERE operation_id = ?
                    """,
                    (
                        int(state),
                        provider_operation_ref,
                        reason_code,
                        _dump_time(completed_at),
                        operation_id,
                    ),
                )
            if observed_state is not None:
                self._conn.execute(
                    """
                    UPDATE profiles
                       SET observed_state = ?, observed_reason_code = ?, last_observed_at = ?
                     WHERE profile_id = ?
                    """,
                    (
                        int(observed_state),
                        reason_code if state is OperationState.FAILED else "",
                        _dump_time(completed_at),
                        current.profile_id,
                    ),
                )
            outcome = "succeeded" if state is OperationState.SUCCEEDED else "failed"
            self._conn.execute(
                """
                INSERT OR IGNORE INTO evidence (
                    evidence_id, profile_id, operation_id, kind, outcome,
                    digest_sha256, size_bytes, observed_at
                ) VALUES (?, ?, ?, 'lifecycle_operation', ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    current.profile_id,
                    operation_id,
                    outcome,
                    digest_sha256,
                    size_bytes,
                    _dump_time(completed_at),
                ),
            )
            final_row = self._conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if final_row is None:
                raise ProfileNotFound("operation was not found")
            return self._operation_from_row(final_row)

    def set_observed_state(
        self,
        profile_id: str,
        *,
        observed_state: ObservedState,
        reason_code: str = "",
    ) -> ProfileRecord:
        if observed_state is ObservedState.UNSPECIFIED:
            raise ValueError("observed state is required")
        if reason_code and not reason_code.replace("_", "").isalnum():
            raise ValueError("reason code must be stable and machine-readable")
        with self._lock, self._conn:
            self.get_profile(profile_id)
            now = _now()
            self._conn.execute(
                """
                UPDATE profiles
                   SET observed_state = ?, observed_reason_code = ?, last_observed_at = ?
                 WHERE profile_id = ?
                """,
                (int(observed_state), reason_code, _dump_time(now), profile_id),
            )
            return self.get_profile(profile_id)

    def record_operation_evidence(
        self,
        *,
        operation: OperationRecord,
        digest_sha256: str,
        size_bytes: int,
    ) -> EvidenceRecord:
        if len(digest_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in digest_sha256):
            raise ValueError("evidence digest must be lowercase SHA-256")
        if size_bytes < 0:
            raise ValueError("evidence size cannot be negative")
        observed_at = operation.completed_at or _now()
        outcome = "succeeded" if operation.state is OperationState.SUCCEEDED else "failed"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO evidence (
                    evidence_id, profile_id, operation_id, kind, outcome,
                    digest_sha256, size_bytes, observed_at
                ) VALUES (?, ?, ?, 'lifecycle_operation', ?, ?, ?, ?)
                """,
                (
                    operation.operation_id,
                    operation.profile_id,
                    operation.operation_id,
                    outcome,
                    digest_sha256,
                    size_bytes,
                    _dump_time(observed_at),
                ),
            )
        return EvidenceRecord(
            evidence_id=operation.operation_id,
            profile_id=operation.profile_id,
            operation_id=operation.operation_id,
            kind="lifecycle_operation",
            outcome=outcome,
            digest_sha256=digest_sha256,
            size_bytes=size_bytes,
            observed_at=observed_at,
        )

    def list_evidence(self, profile_id: str, *, limit: int = 50) -> list[EvidenceRecord]:
        self._require_configured_profile(profile_id)
        if limit < 1 or limit > 100:
            raise ValueError("evidence limit must be between 1 and 100")
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM evidence
                 WHERE profile_id = ?
                 ORDER BY observed_at DESC
                 LIMIT ?
                """,
                (profile_id, limit),
            ).fetchall()
        return [
            EvidenceRecord(
                evidence_id=str(row["evidence_id"]),
                profile_id=str(row["profile_id"]),
                operation_id=str(row["operation_id"]),
                kind=str(row["kind"]),
                outcome=str(row["outcome"]),
                digest_sha256=str(row["digest_sha256"]),
                size_bytes=int(row["size_bytes"]),
                observed_at=_load_time(str(row["observed_at"])) or _now(),
            )
            for row in rows
        ]
