from __future__ import annotations

import importlib.util
from pathlib import Path

from services.hypha_admin_broker.auth import BrokerSessionStore

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "rotate_hypha_admin_broker_secret.py"
BASE = {
    "POSTGRES_PASSWORD": "postgres-password-value-1234567890",
    "REGISTRATION_SHARED_SECRET": "registration-shared-secret-value-1234",
    "MACAROON_SECRET_KEY": "macaroon-secret-key-value-123456789",
    "FORM_SECRET": "form-secret-value-1234567890123456",
}


def load_rotation():
    spec = importlib.util.spec_from_file_location("rotate_hypha_admin_broker_secret", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_first_rotation_preserves_synapse_secrets_and_adds_only_broker_credentials():
    rotation = load_rotation()
    operator_secret = "new-operator-administration-secret-1234"

    updated = rotation.rotated_values(BASE, operator_secret)

    assert {key: updated[key] for key in BASE} == BASE
    assert set(updated) == rotation.BASE_KEYS | rotation.BROKER_KEYS
    assert operator_secret not in updated.values()
    store = BrokerSessionStore(verifier=updated["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"])
    assert store.authenticate(operator_secret, source="test").session_token
    assert len(updated["HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"]) >= 32


def test_later_operator_rotation_preserves_the_live_service_password():
    rotation = load_rotation()
    current = dict(
        BASE,
        HYPHA_ADMIN_BROKER_SECRET_VERIFIER="old-verifier",
        HYPHA_ADMIN_BROKER_SERVICE_PASSWORD="live-service-password-value-123456",
    )

    updated = rotation.rotated_values(current, "replacement-operator-administration-secret")

    assert (
        updated["HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"]
        == current["HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"]
    )
    assert (
        updated["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"]
        != current["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"]
    )


def test_rotation_uses_protected_input_and_never_accepts_raw_secret_on_argv_or_output():
    source = SCRIPT.read_text(encoding="utf-8")

    for marker in [
        "getpass.getpass",
        "NamedTemporaryFile",
        "get-secret-value",
        "put-secret-value",
        "AWSCURRENT",
        "encode_scrypt_verifier",
        "HyphaSynapseDeploymentRole",
    ]:
        assert marker in source
    for forbidden in ["--operator-secret", "print(updated", "print(current"]:
        assert forbidden not in source
