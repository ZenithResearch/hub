from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_gateway_production_task_uses_public_matrix_homeserver_and_identity():
    ecs = read("infra/aws_baseline_80/ecs.tf")

    assert '{ name = "MATRIX_HOMESERVER_URL", value = var.enable_matrix_synapse ? "https://${var.public_matrix_domain_name}" : "" }' in ecs
    assert '{ name = "MATRIX_GATEWAY_BOT_USER_ID", value = var.enable_matrix_synapse ? "@gateway-bot:${var.public_matrix_domain_name}" : "" }' in ecs


def test_enabled_synapse_fails_closed_on_wrong_or_missing_public_identity():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")

    assert 'var.public_matrix_domain_name == "synapse.zenith-research.ca"' in runtime
    assert "Production Matrix requires public_matrix_domain_name" in runtime
    assert "localhost" not in runtime


def test_local_examples_remain_explicit_and_document_production_overrides():
    env = read(".env.example")

    assert "MATRIX_HOMESERVER_URL=http://localhost:8008" in env
    assert "MATRIX_HOMESERVER=http://matrix-synapse:8008" in env
    assert "MATRIX_SERVER_NAME=localhost" in env
    assert "Production: https://synapse.zenith-research.ca" in env
    assert "Production Matrix values are injected by Terraform" in env


def test_issue_handoff_records_accepted_p14_evidence_and_p15_sequence():
    spec = read("docs/issues/matrix-synapse-v0/iss-p15-003-production-homeserver-config.md")

    assert "P14 dependency satisfied" in spec
    assert "iss-p14-007-production.json" in spec
    assert "P15-004 remains blocked until this issue merges" in spec
    assert "P15-005 remains blocked until P15-004" in spec


def test_operator_runbook_distinguishes_local_staging_and_production_config():
    doc = read("docs/operations/matrix-appservice-config.md")

    for environment in ("Local", "Staging", "Production"):
        assert f"| {environment} |" in doc
    for key in (
        "MATRIX_HOMESERVER_URL",
        "MATRIX_HOMESERVER",
        "MATRIX_SERVER_NAME",
        "MATRIX_GATEWAY_BOT_USER_ID",
        "SOPHIA_MATRIX_USER",
    ):
        assert key in doc
    assert "public server identity" in doc
    assert "private transport URL" in doc
    assert "P15-004" in doc
    assert "Do not place raw" in doc
