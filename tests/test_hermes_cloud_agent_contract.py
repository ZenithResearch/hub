from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "infra/hermes_cloud_agent/profile.schema.json"
ARTIFACT_LOCK_PATH = ROOT / "infra/hermes_cloud_agent/artifacts/local-inference.lock.json"
ARTIFACT_LOCK_SCHEMA_PATH = (
    ROOT / "infra/hermes_cloud_agent/artifacts/local-inference-lock.schema.json"
)
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
            "model_id": "qwen3-8b-q4-k-m",
            "model_sha256": "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
            "artifact_lock_sha256": "a" * 64,
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


def test_local_inference_lock_pins_exact_staged_artifacts() -> None:
    assert ARTIFACT_LOCK_PATH.is_file()
    assert ARTIFACT_LOCK_SCHEMA_PATH.is_file()
    lock_schema = json.loads(ARTIFACT_LOCK_SCHEMA_PATH.read_text(encoding="utf-8"))
    lock = json.loads(ARTIFACT_LOCK_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(lock_schema)
    jsonschema.validate(lock, lock_schema)

    desired = lock["desired"]
    assert "rollback" not in lock
    assert desired["llama_cpp"] == {
        "repository": "https://github.com/ggml-org/llama.cpp",
        "commit": "47a39665e7081dc482feec169961acc09750a5c4",
        "release": "b10000",
        "archive_filename": "llama-b10000-bin-ubuntu-x64.tar.gz",
        "archive_sha256": "80faa4e10350436aeb09f01c3f299f6ebeaf3000f21cdf2b0ec4d2299b056274",
        "size_bytes": 15855212,
        "s3_bucket": "zenith-hub-prod-llama-models-044528206149-us-east-1",
        "s3_key": "hermes-cloud-agent/runtime/llama.cpp/b10000/80faa4e10350436aeb09f01c3f299f6ebeaf3000f21cdf2b0ec4d2299b056274/llama-b10000-bin-ubuntu-x64.tar.gz",
        "s3_version_id": "apAEVDVfFYNUu13eZw0gIIaKYZwweTL5",
    }
    assert desired["model"] == {
        "source_repository": "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
        "revision": "7c41481f57cb95916b40956ab2f0b139b296d974",
        "filename": "Qwen3-8B-Q4_K_M.gguf",
        "model_id": "qwen3-8b-q4-k-m",
        "sha256": "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
        "size_bytes": 5027783488,
        "s3_bucket": "zenith-hub-prod-llama-models-044528206149-us-east-1",
        "s3_key": "hermes-cloud-agent/models/Qwen3-8B/7c41481f57cb95916b40956ab2f0b139b296d974/d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785/Qwen3-8B-Q4_K_M.gguf",
        "s3_version_id": "pNL9.QCQfHICIn7qYcL5kA5WRSBStpuo",
        "license": "apache-2.0",
        "context_length": 32768,
        "chat_template": "jinja",
        "tool_calling_verified": False,
    }


@pytest.mark.parametrize(
    ("component", "field", "value"),
    [
        ("llama_cpp", "release", "latest"),
        ("llama_cpp", "archive_sha256", "A" * 64),
        ("llama_cpp", "s3_version_id", "null"),
        ("llama_cpp", "s3_key", "hermes-cloud-agent/runtime/*"),
        ("model", "revision", "main"),
        ("model", "s3_key", "../Qwen3-8B-Q4_K_M.gguf"),
        ("model", "s3_version_id", ""),
    ],
)
def test_local_inference_lock_rejects_mutable_or_unsafe_artifacts(
    component: str, field: str, value: object
) -> None:
    lock_schema = json.loads(ARTIFACT_LOCK_SCHEMA_PATH.read_text(encoding="utf-8"))
    lock = json.loads(ARTIFACT_LOCK_PATH.read_text(encoding="utf-8"))
    lock["desired"][component][field] = value

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(lock, lock_schema)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("matrix", "e2ee_mode"), "optional"),
        (("gateway", "api_server_enabled"), True),
        (("inference", "base_url"), "https://inference.example.com/v1"),
        (("inference", "base_url"), "http://localhost:8080/v1"),
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
