from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_fresh_admin_provisioner_is_exact_role_secret_endpoint_and_identity_scoped():
    script = (ROOT / "scripts/provision_fresh_synapse_admin.py").read_text(encoding="utf-8")
    policy = (ROOT / "infra/matrix/aws/bootstrap.yaml").read_text(encoding="utf-8")

    for marker in [
        'EXPECTED_PROFILE = "zenith-hypha-synapse"',
        'EXPECTED_ACCOUNT = "610992396917"',
        'EXPECTED_REGION = "us-east-1"',
        'EXPECTED_SECRET_NAME = "hypha/fresh-synapse/runtime"',
        'EXPECTED_ENDPOINT = "https://synapse.zenith-research.ca"',
        'EXPECTED_USERNAME = "beaver"',
        'EXPECTED_USER_ID = "@beaver:synapse.zenith-research.ca"',
        "assumed-role/HyphaSynapseDeploymentRole/",
        '"REGISTRATION_SHARED_SECRET"',
        "provision_admins",
        "store_in_keychain",
    ]:
        assert marker in script

    assert "secretsmanager:GetSecretValue" in policy
    assert "arn:aws:secretsmanager:us-east-1:610992396917:secret:hypha/fresh-synapse/runtime-*" in policy
    for forbidden in ["zenith-hermes", "zenith-hub-prod", "banana", "mgpi", "print(secret", "print(password"]:
        assert forbidden not in script
