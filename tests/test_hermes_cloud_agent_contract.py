from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "infra/hermes_cloud_agent/profile.schema.json"


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
            "access_token_secret_ref": "aws-secretsmanager:hermes/cloudproof/matrix-token",
            "crypto_store": "/var/lib/hermes/profiles/cloudproof/matrix-crypto",
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
        "sandbox": {"backend": "docker"},
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
