import importlib.util
from pathlib import Path

from services.hypha_admin_broker.auth import BrokerSessionStore

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "populate_fresh_synapse_secret.py"


def load_population():
    spec = importlib.util.spec_from_file_location("populate_fresh_synapse_secret", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_secret_population_is_role_scoped_fresh_and_non_printing():
    script = SCRIPT.read_text(encoding="utf-8")

    for marker in [
        'EXPECTED_PROFILE = "zenith-hypha-synapse"',
        'EXPECTED_ACCOUNT = "610992396917"',
        'EXPECTED_REGION = "us-east-1"',
        'EXPECTED_SECRET_NAME = "hypha/fresh-synapse/runtime"',
        "assumed-role/HyphaSynapseDeploymentRole/",
        "secrets.token_urlsafe",
        '"POSTGRES_PASSWORD"',
        '"REGISTRATION_SHARED_SECRET"',
        '"MACAROON_SECRET_KEY"',
        '"FORM_SECRET"',
        '"HYPHA_ADMIN_BROKER_SECRET_VERIFIER"',
        '"HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"',
        "encode_scrypt_verifier",
        "getpass.getpass",
        "put-secret-value",
        "AWSCURRENT",
        "NamedTemporaryFile",
        "os.chmod",
        "0o600",
        '"AWS_ACCESS_KEY_ID"',
        '"AWS_SECRET_ACCESS_KEY"',
        '"AWS_SESSION_TOKEN"',
        "environment.pop",
    ]:
        assert marker in script

    for forbidden in [
        "SecretString",
        "print(values",
        "print(payload",
        "capture_output=False",
        "--operator-secret",
    ]:
        assert forbidden not in script


def test_fresh_secret_contains_exact_runtime_schema_and_only_a_broker_verifier():
    population = load_population()
    operator_secret = "operator-administration-secret-value-1234"

    values = population._fresh_values(operator_secret)

    assert set(values) == set(population.REQUIRED_KEYS)
    assert operator_secret not in values.values()
    store = BrokerSessionStore(verifier=values["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"])
    assert store.authenticate(operator_secret, source="test").session_token
    assert len(values["HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"]) >= 32


def test_bootstrap_policy_allows_only_exact_secret_population_target():
    template = (ROOT / "infra/matrix/aws/bootstrap.yaml").read_text(encoding="utf-8")

    assert "secretsmanager:PutSecretValue" in template
    assert "arn:aws:secretsmanager:us-east-1:610992396917:secret:hypha/fresh-synapse/runtime-*" in template
    assert "secretsmanager:*" not in template
