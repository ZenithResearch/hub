from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_secret_population_is_role_scoped_fresh_and_non_printing():
    script = (ROOT / "scripts/populate_fresh_synapse_secret.py").read_text(encoding="utf-8")

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
    ]:
        assert forbidden not in script


def test_bootstrap_policy_allows_only_exact_secret_population_target():
    template = (ROOT / "infra/matrix/aws/bootstrap.yaml").read_text(encoding="utf-8")

    assert "secretsmanager:PutSecretValue" in template
    assert "arn:aws:secretsmanager:us-east-1:610992396917:secret:hypha/fresh-synapse/runtime-*" in template
    assert "secretsmanager:*" not in template
