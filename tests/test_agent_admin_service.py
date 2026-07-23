from __future__ import annotations

import asyncio
import time
from pathlib import Path

import grpc
import pytest

from libs.common.proto import agent_admin_pb2
from services.agent_admin.domain import DesiredState, LifecycleAction, ObservedState, OperationState
from services.agent_admin.service import AgentAdminService
from services.agent_admin.ssm import SsmDispatchError, SsmObservation
from services.agent_admin.store import AgentAdminStore

ALLOWED_ARN = "arn:aws:secretsmanager:us-west-2:123456789012:secret:matrix"
COMMAND_ID = "12345678-abcd-1234-abcd-123456789012"


class RpcAbort(RuntimeError):
    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        self.code = code
        self.details = details
        super().__init__(details)


class FakeContext:
    async def abort(self, code: grpc.StatusCode, details: str):
        raise RpcAbort(code, details)


class FakeDispatcher:
    def __init__(self) -> None:
        self.dispatched = []
        self.dispatch_error = False
        self.dispatch_delay = 0.0
        self.observation = SsmObservation(
            OperationState.SUCCEEDED,
            "",
            ObservedState.RUNNING,
        )

    def dispatch(self, action):
        self.dispatched.append(action)
        if self.dispatch_delay:
            time.sleep(self.dispatch_delay)
        if self.dispatch_error:
            raise SsmDispatchError("provider details must not escape")
        return COMMAND_ID

    def observe(self, command_id, action):
        assert command_id == COMMAND_ID
        self.observed_action = action
        return self.observation


def _service(tmp_path: Path) -> tuple[AgentAdminService, AgentAdminStore, FakeDispatcher]:
    store = AgentAdminStore(tmp_path / "agent-admin.db", configured_profile_id="cloudproof")
    dispatcher = FakeDispatcher()
    service = AgentAdminService(
        store=store,
        dispatcher=dispatcher,
        configured_profile_id="cloudproof",
        allowed_matrix_secret_arns={ALLOWED_ARN},
    )
    return service, store, dispatcher


def test_service_dispatches_typed_lifecycle_and_updates_observed_state_only_after_success(
    tmp_path: Path,
) -> None:
    service, store, dispatcher = _service(tmp_path)
    context = FakeContext()

    profile = asyncio.run(
        service.RegisterProfile(
            agent_admin_pb2.RegisterProfileRequest(profile_id="cloudproof"), context
        )
    )
    assert profile.revision == 1
    profile = asyncio.run(
        service.SetMatrixCredentialReference(
            agent_admin_pb2.SetMatrixCredentialReferenceRequest(
                profile_id="cloudproof",
                secret_arn=ALLOWED_ARN,
                expected_revision=1,
            ),
            context,
        )
    )
    assert profile.revision == 2
    profile = asyncio.run(
        service.SetDesiredState(
            agent_admin_pb2.SetDesiredStateRequest(
                profile_id="cloudproof",
                desired_state=agent_admin_pb2.DESIRED_STATE_ENABLED,
                expected_revision=2,
            ),
            context,
        )
    )
    assert profile.observed_state == agent_admin_pb2.OBSERVED_STATE_UNKNOWN

    operation = asyncio.run(
        service.RequestLifecycleOperation(
            agent_admin_pb2.RequestLifecycleOperationRequest(
                profile_id="cloudproof",
                action=agent_admin_pb2.LIFECYCLE_ACTION_RESTART,
                expected_revision=3,
                idempotency_key="restart-1",
            ),
            context,
        )
    )
    assert operation.state == agent_admin_pb2.OPERATION_STATE_DISPATCHED
    assert operation.provider_operation_ref == COMMAND_ID
    assert store.get_profile("cloudproof").observed_state is ObservedState.UNKNOWN

    completed = asyncio.run(
        service.GetLifecycleOperation(
            agent_admin_pb2.GetLifecycleOperationRequest(
                profile_id="cloudproof", operation_id=operation.operation_id
            ),
            context,
        )
    )
    assert completed.state == agent_admin_pb2.OPERATION_STATE_SUCCEEDED
    assert completed.secrets_printed is False
    assert store.get_profile("cloudproof").observed_state is ObservedState.RUNNING
    assert dispatcher.dispatched
    store.close()


def test_service_rejects_unallowlisted_credential_reference(tmp_path: Path) -> None:
    service, store, _ = _service(tmp_path)
    context = FakeContext()
    asyncio.run(
        service.RegisterProfile(
            agent_admin_pb2.RegisterProfileRequest(profile_id="cloudproof"), context
        )
    )

    with pytest.raises(RpcAbort) as exc:
        asyncio.run(
            service.SetMatrixCredentialReference(
                agent_admin_pb2.SetMatrixCredentialReferenceRequest(
                    profile_id="cloudproof",
                    secret_arn="arn:aws:secretsmanager:us-west-2:123456789012:secret:other",
                    expected_revision=1,
                ),
                context,
            )
        )
    assert exc.value.code is grpc.StatusCode.INVALID_ARGUMENT
    assert "other" not in exc.value.details
    store.close()


def test_service_persists_redacted_failure_when_ssm_dispatch_fails(tmp_path: Path) -> None:
    service, store, dispatcher = _service(tmp_path)
    context = FakeContext()
    asyncio.run(
        service.RegisterProfile(
            agent_admin_pb2.RegisterProfileRequest(profile_id="cloudproof"), context
        )
    )
    dispatcher.dispatch_error = True

    operation = asyncio.run(
        service.RequestLifecycleOperation(
            agent_admin_pb2.RequestLifecycleOperationRequest(
                profile_id="cloudproof",
                action=agent_admin_pb2.LIFECYCLE_ACTION_REFRESH_STATUS,
                expected_revision=1,
                idempotency_key="status-failure",
            ),
            context,
        )
    )
    assert operation.state == agent_admin_pb2.OPERATION_STATE_FAILED
    assert operation.reason_code == "ssm_dispatch_failed"
    assert operation.provider_operation_ref == ""
    assert "provider details" not in str(operation)
    store.close()


def test_service_requires_desired_and_credential_preconditions_for_enable(
    tmp_path: Path,
) -> None:
    service, store, _ = _service(tmp_path)
    context = FakeContext()
    asyncio.run(
        service.RegisterProfile(
            agent_admin_pb2.RegisterProfileRequest(profile_id="cloudproof"), context
        )
    )

    with pytest.raises(RpcAbort) as exc:
        asyncio.run(
            service.RequestLifecycleOperation(
                agent_admin_pb2.RequestLifecycleOperationRequest(
                    profile_id="cloudproof",
                    action=agent_admin_pb2.LIFECYCLE_ACTION_ENABLE,
                    expected_revision=1,
                    idempotency_key="unsafe-enable",
                ),
                context,
            )
        )
    assert exc.value.code is grpc.StatusCode.FAILED_PRECONDITION
    store.close()


def test_service_fails_closed_instead_of_redispatching_recovered_pending_operation(
    tmp_path: Path,
) -> None:
    service, store, dispatcher = _service(tmp_path)
    context = FakeContext()
    store.register_profile("cloudproof")
    pending, created = store.create_operation(
        "cloudproof",
        action=LifecycleAction.REFRESH_STATUS,
        expected_revision=1,
        idempotency_key="crash-window",
    )
    assert created is True
    assert pending.state is OperationState.PENDING

    recovered = asyncio.run(
        service.RequestLifecycleOperation(
            agent_admin_pb2.RequestLifecycleOperationRequest(
                profile_id="cloudproof",
                action=agent_admin_pb2.LIFECYCLE_ACTION_REFRESH_STATUS,
                expected_revision=1,
                idempotency_key="crash-window",
            ),
            context,
        )
    )

    assert recovered.state == agent_admin_pb2.OPERATION_STATE_FAILED
    assert recovered.reason_code == "dispatch_state_unknown"
    assert dispatcher.dispatched == []
    store.close()


def test_service_serializes_concurrent_idempotent_lifecycle_requests(tmp_path: Path) -> None:
    service, store, dispatcher = _service(tmp_path)
    context = FakeContext()
    store.register_profile("cloudproof")
    dispatcher.dispatch_delay = 0.05
    request = agent_admin_pb2.RequestLifecycleOperationRequest(
        profile_id="cloudproof",
        action=agent_admin_pb2.LIFECYCLE_ACTION_REFRESH_STATUS,
        expected_revision=1,
        idempotency_key="concurrent-status",
    )

    async def invoke_twice():
        return await asyncio.gather(
            service.RequestLifecycleOperation(request, context),
            service.RequestLifecycleOperation(request, context),
        )

    first, second = asyncio.run(invoke_twice())

    assert first.operation_id == second.operation_id
    assert first.state == agent_admin_pb2.OPERATION_STATE_DISPATCHED
    assert second.state == agent_admin_pb2.OPERATION_STATE_DISPATCHED
    assert len(dispatcher.dispatched) == 1
    store.close()


def test_service_replays_exact_request_before_current_revision_validation(tmp_path: Path) -> None:
    service, store, dispatcher = _service(tmp_path)
    context = FakeContext()
    store.register_profile("cloudproof")
    request = agent_admin_pb2.RequestLifecycleOperationRequest(
        profile_id="cloudproof",
        action=agent_admin_pb2.LIFECYCLE_ACTION_REFRESH_STATUS,
        expected_revision=1,
        idempotency_key="stable-replay",
    )
    first = asyncio.run(service.RequestLifecycleOperation(request, context))
    store.set_desired_state(
        "cloudproof",
        desired_state=DesiredState.ENABLED,
        expected_revision=1,
    )

    replay = asyncio.run(service.RequestLifecycleOperation(request, context))

    assert replay.operation_id == first.operation_id
    assert len(dispatcher.dispatched) == 1
    with pytest.raises(RpcAbort) as exc:
        asyncio.run(
            service.RequestLifecycleOperation(
                agent_admin_pb2.RequestLifecycleOperationRequest(
                    profile_id="cloudproof",
                    action=agent_admin_pb2.LIFECYCLE_ACTION_REFRESH_STATUS,
                    expected_revision=2,
                    idempotency_key="stable-replay",
                ),
                context,
            )
        )
    assert exc.value.code is grpc.StatusCode.FAILED_PRECONDITION
    store.close()


def test_transient_ssm_observation_failure_keeps_operation_dispatchable(tmp_path: Path) -> None:
    service, store, dispatcher = _service(tmp_path)
    context = FakeContext()
    store.register_profile("cloudproof")
    operation = asyncio.run(
        service.RequestLifecycleOperation(
            agent_admin_pb2.RequestLifecycleOperationRequest(
                profile_id="cloudproof",
                action=agent_admin_pb2.LIFECYCLE_ACTION_REFRESH_STATUS,
                expected_revision=1,
                idempotency_key="transient-observation",
            ),
            context,
        )
    )

    def unavailable(command_id, action):
        raise SsmDispatchError("temporary provider failure")

    dispatcher.observe = unavailable
    observed = asyncio.run(
        service.GetLifecycleOperation(
            agent_admin_pb2.GetLifecycleOperationRequest(
                profile_id="cloudproof", operation_id=operation.operation_id
            ),
            context,
        )
    )

    assert observed.state == agent_admin_pb2.OPERATION_STATE_DISPATCHED
    assert store.list_evidence("cloudproof") == []
    store.close()
