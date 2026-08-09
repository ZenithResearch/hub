import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_mas_runtime_is_an_inactive_private_stateful_service_by_default():
    runtime = read("infra/aws_baseline_80/matrix_mas_runtime.tf")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'resource "aws_db_instance" "matrix_mas"' in runtime
    assert 'db_name' in runtime and '"mas"' in runtime
    assert "manage_master_user_password" in runtime and "true" in runtime
    assert "prevent_destroy = true" in runtime
    assert 'resource "aws_ecs_task_definition" "matrix_mas"' in runtime
    assert 'resource "aws_ecs_service" "matrix_mas"' in runtime
    assert "assign_public_ip = false" in runtime
    assert "var.start_matrix_mas_service" in runtime
    assert 'variable "enable_matrix_mas"' in variables
    assert 'variable "enable_matrix_mas_public_edge"' in variables
    assert 'variable "start_matrix_mas_service"' in variables
    assert 'default     = false' in variables


def test_mas_uses_digest_pinned_image_and_file_backed_secrets():
    runtime = read("infra/aws_baseline_80/matrix_mas_runtime.tf")
    variables = read("infra/aws_baseline_80/variables.tf")
    secrets = read("infra/aws_baseline_80/matrix_secrets.tf")
    wrapper = read("infra/matrix/mas/entrypoint.sh")
    dockerfile = read("infra/matrix/mas/Dockerfile")

    assert 'busybox@sha256:' in dockerfile
    assert 'COPY --from=busybox /bin/busybox /bin/sh' in dockerfile
    assert 'ADD --chmod=0444 --checksum=sha256:' in dockerfile
    assert 'ghcr.io/element-hq/matrix-authentication-service@sha256:' in variables
    assert 'resource "aws_secretsmanager_secret" "matrix_mas_synapse_shared_secret"' in secrets
    assert 'resource "aws_secretsmanager_secret" "matrix_mas_encryption_secret"' in secrets
    assert 'resource "aws_secretsmanager_secret" "matrix_mas_signing_key"' in secrets
    assert 'secret_string = var.matrix_mas' not in secrets
    assert 'database.password_file' not in runtime
    for marker in [
        "database:",
        "password_file:",
        "secret_file:",
        "encryption_file:",
        "key_file:",
        "umask 077",
        "exec /usr/local/bin/mas-cli --config",
    ]:
        assert marker in wrapper


def test_mas_image_gate_rejects_every_high_or_critical_vulnerability():
    workflow = read(".github/workflows/mas-image.yml")

    assert "severity: HIGH,CRITICAL" in workflow
    assert "ignore-unfixed: false" in workflow
    assert "ignore-unfixed: true" not in workflow
    assert 'image-ref: ${{ steps.image.outputs.registry }}/${{ env.ECR_REPOSITORY }}@${{ steps.push.outputs.digest }}' in workflow


def test_synapse_shared_secret_uses_task_ephemeral_storage_only():
    synapse = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")

    assert "/tmp/mas-shared-secret" in synapse
    assert "/data/mas-shared-secret" not in synapse


def test_mas_and_synapse_cutover_are_separate_reviewed_gates():
    runtime = read("infra/aws_baseline_80/matrix_mas_runtime.tf")
    synapse = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'variable "matrix_mas_cutover_complete"' in variables
    assert "var.matrix_mas_cutover_complete" in synapse
    assert '"matrix_authentication_service"' in synapse
    assert '"msc4108_enabled": True' in synapse
    assert "start_matrix_mas_service requires enable_matrix_mas" in runtime
    assert "matrix_mas_cutover_complete requires a completed syn2mas migration" in synapse
    assert "var.matrix_mas_cutover_complete ? [" in synapse
    assert "service_registries" not in synapse


def test_gateway_matrix_environment_has_a_separate_inactive_gate():
    ecs = read("infra/aws_baseline_80/ecs.tf")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'variable "enable_matrix_gateway_integration"' in variables
    assert "var.enable_matrix_gateway_integration ? [" in ecs


def test_reviewed_migration_task_mounts_synapse_state_read_only():
    runtime = read("infra/aws_baseline_80/matrix_mas_runtime.tf")
    wrapper = read("infra/matrix/mas/entrypoint.sh")

    assert 'resource "aws_ecs_task_definition" "matrix_mas_migration"' in runtime
    assert 'containerPath = "/synapse-data"' in runtime
    assert "readOnly      = true" in runtime
    assert 'transit_encryption = "ENABLED"' in runtime
    assert "syn2mas check" in runtime
    assert 'if [ "$#" -gt 0 ]' in wrapper
    assert 'resource "aws_security_group_rule" "matrix_mas_to_synapse_efs"' in runtime
    assert 'type                     = "egress"' in runtime


def test_cutover_opens_only_bidirectional_synapse_to_mas_transport():
    runtime = read("infra/aws_baseline_80/matrix_mas_runtime.tf")

    assert 'resource "aws_security_group_rule" "matrix_mas_from_synapse"' in runtime
    assert 'resource "aws_security_group_rule" "matrix_synapse_to_mas"' in runtime
    assert runtime.count("count = var.matrix_mas_cutover_complete ? 1 : 0") >= 2
    assert 'description              = "Synapse delegated authentication to MAS"' in runtime


def test_phase_one_plan_guard_rejects_live_synapse_changes(tmp_path):
    plan = {
        "resource_changes": [{
            "address": "aws_ecs_task_definition.matrix_synapse[0]",
            "change": {"actions": ["delete", "create"]},
        }]
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_matrix_mas_plan.py"), "--phase", "infrastructure", str(path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "aws_ecs_task_definition.matrix_synapse[0]" in result.stdout


def test_phase_one_plan_guard_accepts_only_new_mas_resources(tmp_path):
    plan = {
        "resource_changes": [{
            "address": "aws_db_instance.matrix_mas[0]",
            "change": {"actions": ["create"]},
        }]
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_matrix_mas_plan.py"), "--phase", "infrastructure", str(path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0


def run_plan_guard(tmp_path, phase, address, actions):
    path = tmp_path / f"{phase}.json"
    path.write_text(
        json.dumps({"resource_changes": [{"address": address, "change": {"actions": actions}}]}),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_matrix_mas_plan.py"), "--phase", phase, str(path)],
        capture_output=True,
        text=True,
    )


def test_plan_guard_enforces_exact_phase_and_action_boundaries(tmp_path):
    assert run_plan_guard(
        tmp_path, "infrastructure", "aws_lb_listener_rule.matrix_mas_auth_host[0]", ["create"]
    ).returncode == 1
    assert run_plan_guard(
        tmp_path, "infrastructure", "aws_db_instance.matrix_mas[0]", ["delete", "create"]
    ).returncode == 1
    assert run_plan_guard(
        tmp_path, "cutover", "aws_db_instance.matrix_mas[0]", ["delete"]
    ).returncode == 1
    assert run_plan_guard(
        tmp_path, "migration", "aws_ecs_task_definition.matrix_mas_migration[0]", ["create"]
    ).returncode == 0


def test_phase_one_does_not_publish_the_auth_hostname():
    routes = read("infra/aws_baseline_80/matrix_dns_tls.tf")

    assert 'count = var.enable_matrix_mas_public_edge && var.matrix_hosted_zone_id != "" ? 1 : 0' in routes
    assert 'count = var.enable_matrix_mas_public_edge && var.matrix_hosted_zone_id != "" && var.enable_dual_stack_public_edge ? 1 : 0' in routes


def test_runbook_uses_argument_only_migration_task_overrides():
    runbook = read("docs/operations/matrix-msc4108-mas-rollout.md")

    assert '["syn2mas", "check", "--synapse-config", "/synapse-data/homeserver.yaml"]' in runbook
    assert '["syn2mas", "migrate", "--synapse-config", "/synapse-data/homeserver.yaml", "--dry-run"]' in runbook
    assert "mas-cli --config /run/mas/config.yaml" not in runbook


def test_mas_has_a_dedicated_auth_host_and_only_explicit_compatibility_routes_on_synapse():
    runtime = read("infra/aws_baseline_80/matrix_mas_runtime.tf")
    routes = read("infra/aws_baseline_80/matrix_dns_tls.tf")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'resource "aws_lb_target_group" "matrix_mas"' in runtime
    assert '"/health"' in runtime
    assert 'resource "aws_lb_listener_rule" "matrix_mas_auth_host"' in routes
    assert 'resource "aws_lb_listener_rule" "matrix_mas_compat"' in routes
    assert "var.public_matrix_auth_domain_name" in routes
    assert "auth.zenith-research.ca" in variables
    for path in [
        "/_matrix/client/*/login",
        "/_matrix/client/*/logout",
        "/_matrix/client/*/refresh",
    ]:
        assert path in routes


def test_mas_database_is_backed_up_and_private_health_admin_surfaces_are_not_routed():
    backup = read("infra/aws_baseline_80/matrix_backup.tf")
    routes = read("infra/aws_baseline_80/matrix_dns_tls.tf")
    wrapper = read("infra/matrix/mas/entrypoint.sh")

    assert "aws_db_instance.matrix_mas[0].arn" in backup
    assert "name: health" in wrapper
    assert "0.0.0.0:8081" in wrapper
    assert '"adminapi"' not in wrapper
    assert '"prometheus"' not in wrapper
    assert "/health" not in routes
    assert "/metrics" not in routes
    assert "/api/admin" not in routes


def test_mas_production_inputs_outputs_and_operator_runbook_are_present():
    example = read("infra/aws_baseline_80/terraform.tfvars.example")
    outputs = read("infra/aws_baseline_80/outputs.tf")
    runbook = read("docs/operations/matrix-msc4108-mas-rollout.md")

    assert "enable_matrix_mas" in example and "false" in example
    assert "enable_matrix_mas_public_edge" in example
    assert "start_matrix_mas_service" in example
    assert "matrix_mas_cutover_complete = false" in example
    assert 'output "matrix_mas_service_name"' in outputs
    assert 'output "matrix_mas_postgres_endpoint"' in outputs
    assert "external DNS provider" in runbook
    assert "auth.zenith-research.ca" in runbook
    assert "ALB DNS name" in runbook
    for marker in [
        "syn2mas",
        '"--dry-run"',
        "maintenance window",
        "stop Synapse",
        "not easily reversible",
        "/_matrix/client/v1/auth_metadata",
        '"org.matrix.msc4108":true',
    ]:
        assert marker in runbook
