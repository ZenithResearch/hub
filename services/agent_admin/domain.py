from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


class DesiredState(IntEnum):
    UNSPECIFIED = 0
    ENABLED = 1
    DISABLED = 2


class ObservedState(IntEnum):
    UNSPECIFIED = 0
    UNKNOWN = 1
    RUNNING = 2
    STOPPED = 3
    DEGRADED = 4


class LifecycleAction(IntEnum):
    UNSPECIFIED = 0
    ENABLE = 1
    DISABLE = 2
    RESTART = 3
    REFRESH_STATUS = 4


class OperationState(IntEnum):
    UNSPECIFIED = 0
    PENDING = 1
    DISPATCHED = 2
    SUCCEEDED = 3
    FAILED = 4


@dataclass(frozen=True)
class ProfileRecord:
    profile_id: str
    revision: int
    desired_state: DesiredState
    observed_state: ObservedState
    observed_reason_code: str
    matrix_secret_arn: str
    matrix_credential_active: bool
    updated_at: datetime
    last_observed_at: datetime | None


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    profile_id: str
    action: LifecycleAction
    state: OperationState
    provider_operation_ref: str
    reason_code: str
    requested_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    profile_id: str
    operation_id: str
    kind: str
    outcome: str
    digest_sha256: str
    size_bytes: int
    observed_at: datetime
