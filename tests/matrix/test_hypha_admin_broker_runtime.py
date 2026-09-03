from __future__ import annotations

import pytest

from services.hypha_admin_broker.auth import encode_scrypt_verifier
from services.hypha_admin_broker.config import BrokerConfiguration, BrokerConfigurationError
from services.hypha_admin_broker.main import create_runtime_app

SECRET = "correct-administration-secret-value-1234"


def environment() -> dict[str, str]:
    return {
        "HYPHA_ADMIN_BROKER_SECRET_VERIFIER": encode_scrypt_verifier(
            SECRET,
            salt=b"0123456789abcdef",
            n=2**10,
            r=8,
            p=1,
        ),
        "HYPHA_ADMIN_BROKER_SERVICE_USER_ID": "@_hypha_admin_broker:example.org",
        "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD": "server-only-service-password-value",
    }


def test_runtime_app_accepts_only_server_side_authority_configuration():
    configured = environment()

    app = create_runtime_app(configured)

    assert app.docs_url is None
    rendered = repr(BrokerConfiguration.from_environment(configured))
    for secret in [configured["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"], configured["HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"]]:
        assert secret not in rendered


@pytest.mark.parametrize(
    "missing",
    [
        "HYPHA_ADMIN_BROKER_SECRET_VERIFIER",
        "HYPHA_ADMIN_BROKER_SERVICE_USER_ID",
        "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD",
    ],
)
def test_runtime_app_fails_closed_when_authority_configuration_is_missing(missing: str):
    configured = environment()
    configured.pop(missing)

    with pytest.raises(BrokerConfigurationError) as captured:
        create_runtime_app(configured)

    assert str(captured.value) == "Hypha administration broker configuration is invalid"


def test_runtime_uses_persisted_verifier_as_authority_after_rotation(tmp_path):
    configured = environment()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    verifier_path = state / "operator-secret.verifier"
    configured["HYPHA_ADMIN_BROKER_SECRET_VERIFIER_PATH"] = str(verifier_path)

    app = create_runtime_app(configured)

    assert app.docs_url is None
    assert verifier_path.is_file()
    assert verifier_path.stat().st_mode & 0o777 == 0o600
    assert SECRET not in verifier_path.read_text(encoding="ascii")


@pytest.mark.parametrize(
    "path",
    ["relative/verifier", "", "/" + ("x" * 1_024)],
)
def test_runtime_rejects_unsafe_verifier_paths(path: str):
    configured = environment()
    configured["HYPHA_ADMIN_BROKER_SECRET_VERIFIER_PATH"] = path

    with pytest.raises(BrokerConfigurationError):
        create_runtime_app(configured)
