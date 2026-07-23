from __future__ import annotations

from pathlib import Path

import pytest

from services.agent_admin.domain import LifecycleAction, ObservedState, OperationState
from services.agent_admin.ssm import SsmDispatchError, SsmLifecycleDispatcher, SsmObservation

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "infra/hermes_cloud_agent/runtime/hermes-cloud-agent-control"


class FakeSsmClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.response: dict = {"Command": {"CommandId": "12345678-abcd-1234-abcd-123456789012"}}
        self.invocation: dict = {"Status": "Success"}

    def send_command(self, **kwargs):
        self.sent.append(kwargs)
        return self.response

    def get_command_invocation(self, **kwargs):
        self.last_get = kwargs
        return self.invocation


class InvocationNotVisible(RuntimeError):
    response = {"Error": {"Code": "InvocationDoesNotExist"}}


def _dispatcher(client: FakeSsmClient) -> SsmLifecycleDispatcher:
    return SsmLifecycleDispatcher(
        client=client,
        instance_id="i-0123456789abcdef0",
        document_name="hub-nonprod-hermes-agent-control",
    )


@pytest.mark.parametrize(
    ("action", "value"),
    [
        (LifecycleAction.ENABLE, "enable"),
        (LifecycleAction.DISABLE, "disable"),
        (LifecycleAction.RESTART, "restart"),
        (LifecycleAction.REFRESH_STATUS, "status"),
    ],
)
def test_ssm_dispatch_maps_only_typed_actions(action: LifecycleAction, value: str) -> None:
    client = FakeSsmClient()
    dispatcher = _dispatcher(client)

    command_id = dispatcher.dispatch(action)

    assert command_id == "12345678-abcd-1234-abcd-123456789012"
    assert client.sent == [
        {
            "InstanceIds": ["i-0123456789abcdef0"],
            "DocumentName": "hub-nonprod-hermes-agent-control",
            "Parameters": {"Action": [value]},
            "TimeoutSeconds": 60,
        }
    ]


def test_ssm_dispatch_rejects_unspecified_and_malformed_provider_response() -> None:
    client = FakeSsmClient()
    dispatcher = _dispatcher(client)

    with pytest.raises(SsmDispatchError, match="unsupported"):
        dispatcher.dispatch(LifecycleAction.UNSPECIFIED)
    assert client.sent == []

    client.response = {"Command": {"CommandId": "not a command id"}}
    with pytest.raises(SsmDispatchError, match="malformed"):
        dispatcher.dispatch(LifecycleAction.RESTART)


@pytest.mark.parametrize(
    ("provider_status", "state", "reason_code", "observed_state"),
    [
        ("Success", OperationState.SUCCEEDED, "", ObservedState.RUNNING),
        ("InProgress", OperationState.DISPATCHED, "", None),
        ("Pending", OperationState.DISPATCHED, "", None),
        ("Failed", OperationState.FAILED, "ssm_failed", None),
        ("TimedOut", OperationState.FAILED, "ssm_timeout", None),
        ("Cancelled", OperationState.FAILED, "ssm_cancelled", None),
    ],
)
def test_ssm_observation_returns_only_bounded_state(
    provider_status: str,
    state: OperationState,
    reason_code: str,
    observed_state: ObservedState | None,
) -> None:
    client = FakeSsmClient()
    client.invocation = {
        "Status": provider_status,
        "StandardOutputContent": '{"ok":true,"state":"running","secrets_printed":false}',
        "StandardErrorContent": "must not escape",
    }
    result = _dispatcher(client).observe(
        "12345678-abcd-1234-abcd-123456789012",
        LifecycleAction.RESTART,
    )

    assert result.state is state
    assert result.reason_code == reason_code
    assert result.observed_state is observed_state
    assert not hasattr(result, "stdout")
    assert not hasattr(result, "stderr")


def test_status_observation_accepts_only_bounded_json() -> None:
    client = FakeSsmClient()
    client.invocation = {
        "Status": "Success",
        "StandardOutputContent": '{"ok":true,"state":"stopped","secrets_printed":false}\n',
    }
    result = _dispatcher(client).observe(
        "12345678-abcd-1234-abcd-123456789012",
        LifecycleAction.REFRESH_STATUS,
    )
    assert result.observed_state is ObservedState.STOPPED

    client.invocation["StandardOutputContent"] = "arbitrary output"
    malformed = _dispatcher(client).observe(
        "12345678-abcd-1234-abcd-123456789012",
        LifecycleAction.REFRESH_STATUS,
    )
    assert malformed == SsmObservation(
        OperationState.FAILED, "invalid_provider_evidence", None
    )


def test_lifecycle_success_requires_verified_postcondition() -> None:
    client = FakeSsmClient()
    client.invocation = {
        "Status": "Success",
        "StandardOutputContent": '{"ok":true,"state":"stopped","secrets_printed":false}',
    }

    mismatch = _dispatcher(client).observe(
        "12345678-abcd-1234-abcd-123456789012",
        LifecycleAction.RESTART,
    )

    assert mismatch == SsmObservation(
        OperationState.FAILED,
        "postcondition_failed",
        ObservedState.STOPPED,
    )


def test_node_control_helper_has_fixed_actions_and_no_shell_evaluation() -> None:
    control = CONTROL.read_text(encoding="utf-8")

    assert 'case "$action" in' in control
    for action in ("enable)", "disable)", "restart)", "status)"):
        assert action in control
    for forbidden in ("eval ", "bash -c", "sh -c", "journalctl", "cat "):
        assert forbidden not in control


def test_ssm_observation_treats_eventual_consistency_as_still_dispatched() -> None:
    client = FakeSsmClient()

    def not_visible(**kwargs):
        raise InvocationNotVisible("provider details must not escape")

    client.get_command_invocation = not_visible
    result = _dispatcher(client).observe(
        "12345678-abcd-1234-abcd-123456789012",
        LifecycleAction.REFRESH_STATUS,
    )

    assert result == SsmObservation(OperationState.DISPATCHED, "", None)
