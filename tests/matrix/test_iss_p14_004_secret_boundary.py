from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def read(rel: str) -> str:
    return (ROOT / rel).read_text()

def test_matrix_secret_classes_are_secrets_manager_backed_and_sensitive():
    variables = read("infra/aws_baseline_80/variables.tf")
    secrets = read("infra/aws_baseline_80/matrix_secrets.tf")
    for name in [
        "matrix_homeserver_signing_key",
        "matrix_macaroon_secret_key",
        "matrix_registration_shared_secret",
        "matrix_form_secret",
        "matrix_appservice_as_token",
        "matrix_appservice_hs_token",
    ]:
        assert f'variable "{name}"' in variables
        assert 'sensitive   = true' in variables
    for resource in [
        'matrix_homeserver_signing_key',
        'matrix_macaroon_secret_key',
        'matrix_registration_shared_secret',
        'matrix_form_secret',
        'matrix_appservice_as_token',
        'matrix_appservice_hs_token',
    ]:
        assert f'resource "aws_secretsmanager_secret" "{resource}"' in secrets
        assert f'resource "aws_secretsmanager_secret_version" "{resource}"' in secrets

def test_matrix_secret_docs_record_rotation_owner_and_no_raw_tokens():
    doc = read("docs/operations/matrix-secrets.md")
    assert 'Rotation owner' in doc
    assert 'Do not print raw Matrix' in doc
    assert 'Generated appservice registration files are runtime artifacts' in doc
    assert 'terraform.tfvars' in doc and 'must not contain raw production Matrix secrets' in doc

def test_matrix_secret_committed_examples_are_placeholders_only():
    secrets = read("infra/aws_baseline_80/matrix_secrets.tf")
    forbidden_literals = ['as_token:', 'hs_token:', 'registration_shared_secret:', 'macaroon_secret_key:']
    for literal in forbidden_literals:
        assert literal not in secrets
