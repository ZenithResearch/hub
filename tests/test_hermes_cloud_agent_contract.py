from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "infra/hermes_cloud_agent/profile.schema.json"
ISSUE_SPEC_PATH = ROOT / "docs/issues/hermes-cloud-agent-v0/issue-97-matrix-only-profiled-agent.md"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_config() -> dict:
    return {
        "schema_version": 1,
        "profile": {
            "id": "cloudproof",
            "home": "/var/lib/hermes/profiles/cloudproof",
        },
        "matrix": {
            "homeserver": "https://synapse.zenith-research.ca",
            "user_id": "@cloudproof:zenith-research.ca",
            "access_token_secret_ref": (
                "aws-secretsmanager:arn:aws:secretsmanager:us-west-2:123456789012:"
                "secret:hermes/cloudproof/matrix-token-AbCdEf"
            ),
            "crypto_store": "/var/lib/hermes/profiles/cloudproof/platforms/matrix/store",
            "e2ee_mode": "required",
            "allowed_users": ["@operator:zenith-research.ca"],
            "allowed_rooms": ["!proof:zenith-research.ca"],
            "session_scope": "room",
        },
        "gateway": {"api_server_enabled": False},
        "inference": {
            "provider": "custom",
            "base_url": "http://127.0.0.1:8080/v1",
            "model_id": "qwen3.5-9b-q4-k-m",
            "model_sha256": "a" * 64,
            "fallbacks": [],
        },
        "sandbox": {
            "backend": "docker",
            "network": False,
            "host_mounts": False,
            "credential_passthrough": False,
            "allowed_toolsets": ["clarify", "file", "memory", "terminal", "todo"],
        },
        "storage": {"encrypted": True},
        "operations": {
            "administration": "ssm",
            "public_ssh": False,
            "public_agent_ingress": False,
        },
    }


def test_schema_accepts_matrix_only_local_inference_contract() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())
    jsonschema.validate(_valid_config(), _schema())


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("matrix", "e2ee_mode"), "optional"),
        (("gateway", "api_server_enabled"), True),
        (("inference", "base_url"), "https://inference.example.com/v1"),
        (("inference", "fallbacks"), ["openrouter"]),
        (("sandbox", "backend"), "local"),
        (("sandbox", "network"), True),
        (("sandbox", "host_mounts"), True),
        (("sandbox", "credential_passthrough"), True),
        (("sandbox", "allowed_toolsets"), ["hermes-matrix"]),
        (("storage", "encrypted"), False),
        (("operations", "administration"), "ssh"),
        (("operations", "public_ssh"), True),
        (("operations", "public_agent_ingress"), True),
    ],
)
def test_schema_rejects_boundary_widening(path: tuple[str, str], value: object) -> None:
    config = copy.deepcopy(_valid_config())
    config[path[0]][path[1]] = value

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(config, _schema())


def test_schema_requires_nonempty_matrix_allowlists() -> None:
    for key in ("allowed_users", "allowed_rooms"):
        config = copy.deepcopy(_valid_config())
        config["matrix"][key] = []

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, _schema())


def test_schema_requires_hermes_native_profile_matrix_store() -> None:
    config = copy.deepcopy(_valid_config())
    config["matrix"]["crypto_store"] = "/var/lib/hermes/profiles/cloudproof/matrix-crypto"

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(config, _schema())


def test_issue_spec_separates_private_admin_control_from_agent_ingress() -> None:
    spec = ISSUE_SPEC_PATH.read_text(encoding="utf-8")

    required = (
        "Agent Admin Service",
        "internal gRPC",
        "authenticated Gateway admin HTTP edge",
        "desired and observed state",
        "AWS Systems Manager",
        "does not accept prompts or arbitrary tool calls",
        "generic Hermes HTTP/API control surface remains disabled",
    )

    for phrase in required:
        assert phrase in spec


def test_issue_spec_requires_a_dedicated_matrix_user_device_without_appservice_authority() -> None:
    spec = ISSUE_SPEC_PATH.read_text(encoding="utf-8")

    assert "dedicated normal Matrix account and stable device" in spec
    assert "per-profile credential namespace" in spec
    assert "Matrix user/device access token" in spec
    assert "not an application-service or namespace-impersonation token" in spec
    assert "raw credential values" in spec
