from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .domain import LifecycleAction, ObservedState, OperationState

_COMMAND_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,17}$")
_DOCUMENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,128}$")
_ACTION_VALUES = {
    LifecycleAction.ENABLE: "enable",
    LifecycleAction.DISABLE: "disable",
    LifecycleAction.RESTART: "restart",
    LifecycleAction.REFRESH_STATUS: "status",
}


class SsmDispatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SsmObservation:
    state: OperationState
    reason_code: str
    observed_state: ObservedState | None


class SsmLifecycleDispatcher:
    def __init__(self, *, client: Any, instance_id: str, document_name: str) -> None:
        if not _INSTANCE_ID_RE.fullmatch(instance_id):
            raise ValueError("invalid configured EC2 instance ID")
        if not _DOCUMENT_NAME_RE.fullmatch(document_name):
            raise ValueError("invalid configured SSM document name")
        self._client = client
        self._instance_id = instance_id
        self._document_name = document_name

    def dispatch(self, action: LifecycleAction) -> str:
        action_value = _ACTION_VALUES.get(action)
        if action_value is None:
            raise SsmDispatchError("unsupported lifecycle action")
        try:
            response = self._client.send_command(
                InstanceIds=[self._instance_id],
                DocumentName=self._document_name,
                Parameters={"Action": [action_value]},
                TimeoutSeconds=60,
            )
        except Exception as exc:
            raise SsmDispatchError("SSM dispatch failed") from exc
        command = response.get("Command") if isinstance(response, dict) else None
        command_id = command.get("CommandId") if isinstance(command, dict) else None
        if not isinstance(command_id, str) or not _COMMAND_ID_RE.fullmatch(command_id):
            raise SsmDispatchError("SSM returned a malformed operation reference")
        return command_id

    def observe(self, command_id: str, action: LifecycleAction) -> SsmObservation:
        if not _COMMAND_ID_RE.fullmatch(command_id):
            raise SsmDispatchError("invalid SSM operation reference")
        try:
            response = self._client.get_command_invocation(
                CommandId=command_id,
                InstanceId=self._instance_id,
            )
        except Exception as exc:
            provider_response = getattr(exc, "response", None)
            error = provider_response.get("Error") if isinstance(provider_response, dict) else None
            error_code = error.get("Code") if isinstance(error, dict) else None
            if error_code == "InvocationDoesNotExist":
                return SsmObservation(OperationState.DISPATCHED, "", None)
            raise SsmDispatchError("SSM observation failed") from exc
        status = response.get("Status") if isinstance(response, dict) else None
        if status == "Success":
            output = response.get("StandardOutputContent")
            if not isinstance(output, str):
                return SsmObservation(
                    OperationState.FAILED, "invalid_provider_evidence", None
                )
            try:
                payload = json.loads(output)
            except (TypeError, json.JSONDecodeError):
                return SsmObservation(
                    OperationState.FAILED, "invalid_provider_evidence", None
                )
            valid_payloads = {
                "running": {
                    "ok": True,
                    "state": "running",
                    "secrets_printed": False,
                },
                "stopped": {
                    "ok": True,
                    "state": "stopped",
                    "secrets_printed": False,
                },
            }
            if payload not in valid_payloads.values():
                return SsmObservation(
                    OperationState.FAILED, "invalid_provider_evidence", None
                )
            observed_state = (
                ObservedState.RUNNING
                if payload == valid_payloads["running"]
                else ObservedState.STOPPED
            )
            expected_state = {
                LifecycleAction.ENABLE: ObservedState.RUNNING,
                LifecycleAction.RESTART: ObservedState.RUNNING,
                LifecycleAction.DISABLE: ObservedState.STOPPED,
            }.get(action)
            if action is not LifecycleAction.REFRESH_STATUS and expected_state is None:
                raise SsmDispatchError("unsupported lifecycle action")
            if expected_state is not None and observed_state is not expected_state:
                return SsmObservation(
                    OperationState.FAILED,
                    "postcondition_failed",
                    observed_state,
                )
            return SsmObservation(OperationState.SUCCEEDED, "", observed_state)
        if status in {"Pending", "InProgress", "Delayed"}:
            return SsmObservation(OperationState.DISPATCHED, "", None)
        reason_codes = {
            "Failed": "ssm_failed",
            "TimedOut": "ssm_timeout",
            "Cancelled": "ssm_cancelled",
            "Cancelling": "ssm_cancelled",
        }
        if isinstance(status, str) and status in reason_codes:
            return SsmObservation(OperationState.FAILED, reason_codes[status], None)
        raise SsmDispatchError("SSM returned an unknown operation state")
