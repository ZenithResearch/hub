from __future__ import annotations

from pathlib import Path

import pytest

from services.agent_admin.domain import DesiredState, LifecycleAction, ObservedState, OperationState
from services.agent_admin.store import (
    AgentAdminStore,
    InvalidTransition,
    ProfileNotFound,
    RevisionConflict,
)


def _store(path: Path) -> AgentAdminStore:
    return AgentAdminStore(path, configured_profile_id="cloudproof")


def test_store_enforces_one_configured_profile_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "agent-admin.db"
    store = _store(path)

    profile = store.register_profile("cloudproof")
    assert profile.profile_id == "cloudproof"
    assert profile.revision == 1
    assert profile.desired_state is DesiredState.DISABLED
    store.close()

    reopened = _store(path)
    assert reopened.get_profile("cloudproof") == profile
    with pytest.raises(ValueError, match="configured profile"):
        reopened.register_profile("other-profile")
    reopened.close()


def test_store_uses_compare_and_swap_revisions(tmp_path: Path) -> None:
    store = _store(tmp_path / "agent-admin.db")
    store.register_profile("cloudproof")

    enabled = store.set_desired_state(
        "cloudproof",
        desired_state=DesiredState.ENABLED,
        expected_revision=1,
    )
    assert enabled.revision == 2
    assert enabled.desired_state is DesiredState.ENABLED

    with pytest.raises(RevisionConflict):
        store.set_desired_state(
            "cloudproof",
            desired_state=DesiredState.DISABLED,
            expected_revision=1,
        )
    unregistered = _store(tmp_path / "unregistered.db")
    with pytest.raises(ProfileNotFound):
        unregistered.get_profile("cloudproof")
    unregistered.close()
    store.close()


def test_credential_reference_is_redacted_and_deactivation_requires_disabled(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "agent-admin.db")
    store.register_profile("cloudproof")
    configured = store.set_matrix_credential_reference(
        "cloudproof",
        secret_arn="arn:aws:secretsmanager:us-west-2:123456789012:secret:matrix",
        expected_revision=1,
    )
    assert configured.matrix_secret_arn.endswith(":secret:matrix")
    assert configured.matrix_credential_active is True

    store.set_desired_state(
        "cloudproof",
        desired_state=DesiredState.ENABLED,
        expected_revision=2,
    )
    with pytest.raises(InvalidTransition, match="disabled"):
        store.deactivate_matrix_credential_reference("cloudproof", expected_revision=3)

    store.set_desired_state(
        "cloudproof",
        desired_state=DesiredState.DISABLED,
        expected_revision=3,
    )
    deactivated = store.deactivate_matrix_credential_reference(
        "cloudproof", expected_revision=4
    )
    assert deactivated.matrix_credential_active is False
    assert deactivated.matrix_secret_arn == ""
    store.close()


def test_lifecycle_operations_are_idempotent_and_never_store_output(tmp_path: Path) -> None:
    store = _store(tmp_path / "agent-admin.db")
    store.register_profile("cloudproof")

    first, first_created = store.create_operation(
        "cloudproof",
        action=LifecycleAction.REFRESH_STATUS,
        expected_revision=1,
        idempotency_key="status-1",
    )
    second, second_created = store.create_operation(
        "cloudproof",
        action=LifecycleAction.REFRESH_STATUS,
        expected_revision=1,
        idempotency_key="status-1",
    )
    assert second == first
    assert first_created is True
    assert second_created is False
    assert first.state is OperationState.PENDING

    dispatched = store.update_operation(
        first.operation_id,
        state=OperationState.DISPATCHED,
        provider_operation_ref="command-id",
        reason_code="",
    )
    assert dispatched.provider_operation_ref == "command-id"

    failed = store.update_operation(
        first.operation_id,
        state=OperationState.FAILED,
        provider_operation_ref="command-id",
        reason_code="ssm_timeout",
    )
    assert failed.reason_code == "ssm_timeout"
    assert not hasattr(failed, "stdout")
    assert not hasattr(failed, "stderr")

    repeated = store.update_operation(
        first.operation_id,
        state=OperationState.FAILED,
        provider_operation_ref="command-id",
        reason_code="ssm_timeout",
    )
    assert repeated == failed
    store.close()


def test_terminal_finalization_atomically_updates_profile_and_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path / "agent-admin.db")
    store.register_profile("cloudproof")
    operation, _ = store.create_operation(
        "cloudproof",
        action=LifecycleAction.REFRESH_STATUS,
        expected_revision=1,
        idempotency_key="atomic-finalization",
    )
    dispatched = store.update_operation(
        operation.operation_id,
        state=OperationState.DISPATCHED,
        provider_operation_ref="command-id",
        reason_code="",
    )

    finalized = store.finalize_operation(
        dispatched.operation_id,
        state=OperationState.SUCCEEDED,
        provider_operation_ref="command-id",
        reason_code="",
        observed_state=ObservedState.STOPPED,
        digest_sha256="a" * 64,
        size_bytes=10,
    )
    repeated = store.finalize_operation(
        dispatched.operation_id,
        state=OperationState.SUCCEEDED,
        provider_operation_ref="command-id",
        reason_code="",
        observed_state=ObservedState.STOPPED,
        digest_sha256="a" * 64,
        size_bytes=10,
    )

    assert finalized == repeated
    assert store.get_profile("cloudproof").observed_state is ObservedState.STOPPED
    assert len(store.list_evidence("cloudproof")) == 1
    store.close()
