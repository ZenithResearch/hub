from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "proto/agent_admin.proto"
CI_CHECK = ROOT / "scripts/ci_check.sh"


def _proto() -> str:
    return PROTO.read_text(encoding="utf-8")


def test_agent_admin_contract_is_separate_and_bounded() -> None:
    proto = _proto()

    assert "package agent.admin.v1;" in proto
    assert "service AgentAdmin" in proto
    for rpc in (
        "RegisterProfile",
        "GetProfile",
        "SetDesiredState",
        "SetMatrixCredentialReference",
        "DeactivateMatrixCredentialReference",
        "RequestLifecycleOperation",
        "GetLifecycleOperation",
        "ListEvidence",
    ):
        assert f"rpc {rpc}(" in proto

    assert "SubmitUserMessage" not in proto
    assert "InvokeTool" not in proto
    assert "google.protobuf.Struct" not in proto


def test_agent_admin_contract_has_no_generic_execution_or_raw_secret_fields() -> None:
    proto = _proto()
    forbidden_fields = {
        "prompt",
        "message",
        "tool",
        "command",
        "arguments",
        "environment",
        "secret_value",
        "secret_bytes",
        "stdout",
        "stderr",
        "filesystem_path",
        "document_name",
        "document_parameters",
    }

    declared_fields = set(
        re.findall(r"^\s*(?:repeated\s+)?[\w.<>]+\s+(\w+)\s*=\s*\d+;", proto, re.MULTILINE)
    )
    assert forbidden_fields.isdisjoint(declared_fields)
    assert "secret_arn" in declared_fields
    assert "provider_operation_ref" in declared_fields


def test_agent_admin_contract_uses_typed_state_and_revision_controls() -> None:
    proto = _proto()

    for enum_name in ("DesiredState", "ObservedState", "LifecycleAction", "OperationState"):
        assert f"enum {enum_name}" in proto
    assert "uint64 expected_revision" in proto
    assert "uint64 revision" in proto
    assert "string idempotency_key" in proto
    assert "bool secrets_printed" in proto


def test_ci_rejects_stale_generated_proto_stubs() -> None:
    ci_check = CI_CHECK.read_text(encoding="utf-8")

    assert "make proto" in ci_check
    assert "git diff --exit-code -- libs/common/proto" in ci_check
