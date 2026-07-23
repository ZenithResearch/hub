from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, NoReturn

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from libs.common.proto import agent_admin_pb2, agent_admin_pb2_grpc

from .domain import (
    DesiredState,
    EvidenceRecord,
    LifecycleAction,
    ObservedState,
    OperationRecord,
    OperationState,
    ProfileRecord,
)
from .ssm import SsmDispatchError, SsmLifecycleDispatcher
from .store import (
    AgentAdminStore,
    InvalidTransition,
    ProfileNotFound,
    RevisionConflict,
)


def _timestamp(value: datetime | None) -> Timestamp:
    result = Timestamp()
    if value is not None:
        result.FromDatetime(value)
    return result


def _profile_message(record: ProfileRecord) -> agent_admin_pb2.Profile:
    return agent_admin_pb2.Profile(
        profile_id=record.profile_id,
        revision=record.revision,
        desired_state=int(record.desired_state),
        observed_state=int(record.observed_state),
        ssm_managed=True,
        observed_reason_code=record.observed_reason_code,
        matrix_credential=agent_admin_pb2.MatrixCredentialReference(
            secret_arn=record.matrix_secret_arn,
            configured=bool(record.matrix_secret_arn),
            active=record.matrix_credential_active,
            updated_at=_timestamp(record.updated_at),
        ),
        updated_at=_timestamp(record.updated_at),
        last_observed_at=_timestamp(record.last_observed_at),
        secrets_printed=False,
    )


def _operation_message(record: OperationRecord) -> agent_admin_pb2.LifecycleOperation:
    return agent_admin_pb2.LifecycleOperation(
        operation_id=record.operation_id,
        profile_id=record.profile_id,
        action=int(record.action),
        state=int(record.state),
        provider_operation_ref=record.provider_operation_ref,
        reason_code=record.reason_code,
        requested_at=_timestamp(record.requested_at),
        completed_at=_timestamp(record.completed_at),
        secrets_printed=False,
    )


def _evidence_message(record: EvidenceRecord) -> agent_admin_pb2.EvidenceSummary:
    return agent_admin_pb2.EvidenceSummary(
        evidence_id=record.evidence_id,
        profile_id=record.profile_id,
        operation_id=record.operation_id,
        kind=record.kind,
        outcome=record.outcome,
        digest_sha256=record.digest_sha256,
        size_bytes=record.size_bytes,
        observed_at=_timestamp(record.observed_at),
    )


class AgentAdminService(agent_admin_pb2_grpc.AgentAdminServicer):
    def __init__(
        self,
        *,
        store: AgentAdminStore,
        dispatcher: SsmLifecycleDispatcher,
        configured_profile_id: str,
        allowed_matrix_secret_arns: set[str],
    ) -> None:
        if not configured_profile_id:
            raise ValueError("configured profile ID is required")
        if not allowed_matrix_secret_arns:
            raise ValueError("at least one Matrix secret ARN must be allowlisted")
        self._store = store
        self._dispatcher = dispatcher
        self._profile_id = configured_profile_id
        self._allowed_secret_arns = frozenset(allowed_matrix_secret_arns)
        self._lifecycle_lock = asyncio.Lock()

    async def _abort(self, context: Any, exc: Exception) -> NoReturn:
        if isinstance(exc, ProfileNotFound):
            code, detail = grpc.StatusCode.NOT_FOUND, "agent admin resource not found"
        elif isinstance(exc, RevisionConflict):
            code, detail = grpc.StatusCode.ABORTED, "profile revision conflict"
        elif isinstance(exc, InvalidTransition):
            code, detail = grpc.StatusCode.FAILED_PRECONDITION, "agent state precondition failed"
        elif isinstance(exc, ValueError):
            code, detail = grpc.StatusCode.INVALID_ARGUMENT, "invalid agent admin request"
        elif isinstance(exc, SsmDispatchError):
            code, detail = grpc.StatusCode.UNAVAILABLE, "agent administration provider unavailable"
        else:
            code, detail = grpc.StatusCode.INTERNAL, "internal agent administration error"
        await context.abort(code, detail)
        raise AssertionError("unreachable")

    def _require_profile_id(self, profile_id: str) -> None:
        if profile_id != self._profile_id:
            raise ValueError("profile does not match configured profile")

    async def RegisterProfile(self, request, context):
        try:
            self._require_profile_id(request.profile_id)
            return _profile_message(self._store.register_profile(request.profile_id))
        except Exception as exc:
            await self._abort(context, exc)

    async def GetProfile(self, request, context):
        try:
            self._require_profile_id(request.profile_id)
            return _profile_message(self._store.get_profile(request.profile_id))
        except Exception as exc:
            await self._abort(context, exc)

    async def SetDesiredState(self, request, context):
        try:
            self._require_profile_id(request.profile_id)
            desired_state = DesiredState(int(request.desired_state))
            record = self._store.set_desired_state(
                request.profile_id,
                desired_state=desired_state,
                expected_revision=int(request.expected_revision),
            )
            return _profile_message(record)
        except Exception as exc:
            await self._abort(context, exc)

    async def SetMatrixCredentialReference(self, request, context):
        try:
            self._require_profile_id(request.profile_id)
            if request.secret_arn not in self._allowed_secret_arns:
                raise ValueError("secret ARN is not allowlisted")
            record = self._store.set_matrix_credential_reference(
                request.profile_id,
                secret_arn=request.secret_arn,
                expected_revision=int(request.expected_revision),
            )
            return _profile_message(record)
        except Exception as exc:
            await self._abort(context, exc)

    async def DeactivateMatrixCredentialReference(self, request, context):
        try:
            self._require_profile_id(request.profile_id)
            record = self._store.deactivate_matrix_credential_reference(
                request.profile_id,
                expected_revision=int(request.expected_revision),
            )
            return _profile_message(record)
        except Exception as exc:
            await self._abort(context, exc)

    @staticmethod
    def _validate_lifecycle_preconditions(
        profile: ProfileRecord,
        action: LifecycleAction,
    ) -> None:
        if action in {LifecycleAction.ENABLE, LifecycleAction.RESTART}:
            if profile.desired_state is not DesiredState.ENABLED:
                raise InvalidTransition("desired state must be enabled")
            if not profile.matrix_credential_active or not profile.matrix_secret_arn:
                raise InvalidTransition("active Matrix credential reference is required")
        elif action is LifecycleAction.DISABLE:
            if profile.desired_state is not DesiredState.DISABLED:
                raise InvalidTransition("desired state must be disabled")
        elif action is not LifecycleAction.REFRESH_STATUS:
            raise ValueError("unsupported lifecycle action")

    async def RequestLifecycleOperation(self, request, context):
        async with self._lifecycle_lock:
            return await self._request_lifecycle_operation(request, context)

    async def _request_lifecycle_operation(self, request, context):
        try:
            self._require_profile_id(request.profile_id)
            action = LifecycleAction(int(request.action))
            existing = self._store.get_operation_by_idempotency(
                request.profile_id,
                action=action,
                expected_revision=int(request.expected_revision),
                idempotency_key=request.idempotency_key,
            )
            if existing is not None:
                if existing.state is OperationState.PENDING:
                    existing = self._finalize_operation(
                        existing,
                        state=OperationState.FAILED,
                        provider_operation_ref="",
                        reason_code="dispatch_state_unknown",
                        observed_state=None,
                    )
                return _operation_message(existing)
            profile = self._store.get_profile(request.profile_id)
            if profile.revision != int(request.expected_revision):
                raise RevisionConflict("profile revision does not match")
            self._validate_lifecycle_preconditions(profile, action)
            operation, created = self._store.create_operation(
                request.profile_id,
                action=action,
                expected_revision=int(request.expected_revision),
                idempotency_key=request.idempotency_key,
            )
            if not created:
                return _operation_message(operation)
            try:
                command_id = await asyncio.to_thread(self._dispatcher.dispatch, action)
            except SsmDispatchError:
                operation = self._finalize_operation(
                    operation,
                    state=OperationState.FAILED,
                    provider_operation_ref="",
                    reason_code="ssm_dispatch_failed",
                    observed_state=None,
                )
                return _operation_message(operation)
            operation = self._store.update_operation(
                operation.operation_id,
                state=OperationState.DISPATCHED,
                provider_operation_ref=command_id,
                reason_code="",
            )
            return _operation_message(operation)
        except Exception as exc:
            await self._abort(context, exc)

    async def GetLifecycleOperation(self, request, context):
        try:
            self._require_profile_id(request.profile_id)
            operation = self._store.get_operation(request.profile_id, request.operation_id)
            if operation.state is OperationState.DISPATCHED:
                try:
                    observation = await asyncio.to_thread(
                        self._dispatcher.observe,
                        operation.provider_operation_ref,
                        operation.action,
                    )
                except SsmDispatchError:
                    return _operation_message(operation)
                if observation.state is not OperationState.DISPATCHED:
                    operation = self._finalize_operation(
                        operation,
                        state=observation.state,
                        provider_operation_ref=operation.provider_operation_ref,
                        reason_code=observation.reason_code,
                        observed_state=observation.observed_state,
                    )
            return _operation_message(operation)
        except Exception as exc:
            await self._abort(context, exc)

    def _finalize_operation(
        self,
        operation: OperationRecord,
        *,
        state: OperationState,
        provider_operation_ref: str,
        reason_code: str,
        observed_state: ObservedState | None,
    ) -> OperationRecord:
        bounded = json.dumps(
            {
                "action": int(operation.action),
                "operation_id": operation.operation_id,
                "outcome": int(state),
                "profile_id": operation.profile_id,
                "reason_code": reason_code,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return self._store.finalize_operation(
            operation.operation_id,
            state=state,
            provider_operation_ref=provider_operation_ref,
            reason_code=reason_code,
            observed_state=observed_state,
            digest_sha256=hashlib.sha256(bounded).hexdigest(),
            size_bytes=len(bounded),
        )

    async def ListEvidence(self, request, context):
        try:
            self._require_profile_id(request.profile_id)
            limit = int(request.limit or 50)
            records = self._store.list_evidence(request.profile_id, limit=limit)
            return agent_admin_pb2.ListEvidenceResponse(
                evidence=[_evidence_message(record) for record in records],
                secrets_printed=False,
            )
        except Exception as exc:
            await self._abort(context, exc)
