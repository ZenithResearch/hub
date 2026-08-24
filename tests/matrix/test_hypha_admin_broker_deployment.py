from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_broker_image_is_digest_pinned_and_routed_only_by_caddy():
    variables = read("infra/matrix/aws/variables.tf")
    main = read("infra/matrix/aws/main.tf")
    user_data = read("infra/matrix/aws/user_data.sh.tpl")

    assert 'variable "admin_broker_image"' in variables
    assert "@sha256:" in variables[variables.index('variable "admin_broker_image"') :]
    assert "admin_broker_image      = var.admin_broker_image" in main
    assert "hypha-admin-broker:8080" in user_data
    assert "handle /_hypha/admin/v1/*" in user_data
    assert "request_body {" in user_data
    assert "max_size 64KB" in user_data
    assert "reverse_proxy hypha-admin-broker:8080" in user_data
    assert '"8080:8080"' not in user_data
    assert '"8008:8008"' not in user_data


def test_broker_container_is_internal_non_root_read_only_and_unprivileged():
    user_data = read("infra/matrix/aws/user_data.sh.tpl")

    for marker in [
        "hypha-admin-broker:",
        "user: \"65532:65532\"",
        "read_only: true",
        "cap_drop:",
        "- ALL",
        "no-new-privileges:true",
        "tmpfs:",
        "networks: [matrix-internal]",
    ]:
        assert marker in user_data
    broker_block = user_data.split("  hypha-admin-broker:", 1)[1].split("  caddy:", 1)[0]
    assert "ports:" not in broker_block
    assert "/var/run/docker.sock" not in broker_block
    assert "/opt/matrix-data" not in broker_block
    assert "matrix-db" not in broker_block


def test_broker_runtime_secret_schema_uses_verifier_and_server_only_service_credential():
    user_data = read("infra/matrix/aws/user_data.sh.tpl")
    population = read("scripts/populate_fresh_synapse_secret.py")

    for marker in [
        '"HYPHA_ADMIN_BROKER_SECRET_VERIFIER"',
        '"HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"',
    ]:
        assert marker in user_data
        assert marker in population
    assert "HYPHA_ADMIN_BROKER_SECRET=" not in user_data
    assert "HYPHA_ADMIN_BROKER_ACCESS_TOKEN" not in user_data


def test_existing_instance_is_updated_by_reviewed_ssm_deployer_with_rollback():
    main = read("infra/matrix/aws/main.tf")
    deployer = read("scripts/deploy_hypha_admin_broker.py")

    assert "ignore_changes = [user_data]" in main
    for marker in [
        "AWS-RunShellScript",
        "HyphaSynapseDeploymentRole",
        "sha256",
        "docker compose",
        "Caddyfile",
        "compose.yaml",
        "rollback",
        "backup",
        "hypha-admin-broker",
        "/_hypha/admin/v1/ready",
        "trap - ERR",
    ]:
        assert marker in deployer
    assert deployer.index("/_hypha/admin/v1/ready") < deployer.index('"trap - ERR"')
    for forbidden in [
        "--secret-value",
        "HYPHA_ADMIN_BROKER_SECRET=",
        "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD=",
    ]:
        assert forbidden not in deployer


def test_broker_image_runs_as_distroless_non_root_entrypoint():
    dockerfile = read("services/hypha_admin_broker/Dockerfile")

    assert "USER 65532:65532" in dockerfile
    assert "COPY --from=" in dockerfile
    assert "services.hypha_admin_broker.main" in dockerfile
    assert "HEALTHCHECK" in dockerfile
