from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_synapse_runtime_owns_compute_database_and_media_state():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'resource "aws_ecs_task_definition" "matrix_synapse"' in runtime
    assert 'resource "aws_ecs_service" "matrix_synapse"' in runtime
    assert 'resource "aws_db_instance" "matrix_synapse"' in runtime
    assert 'resource "aws_efs_file_system" "matrix_synapse"' in runtime
    assert 'resource "aws_efs_access_point" "matrix_synapse"' in runtime
    assert 'resource "aws_efs_mount_target" "matrix_synapse"' in runtime
    assert 'variable "enable_matrix_synapse"' in variables
    assert 'variable "matrix_synapse_image"' in variables
    assert 'variable "matrix_synapse_desired_count"' in variables


def test_synapse_runtime_is_private_and_attached_to_the_matrix_target_group():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")
    dns_tls = read("infra/aws_baseline_80/matrix_dns_tls.tf")

    assert 'assign_public_ip = false' in runtime
    assert 'subnets          = aws_subnet.private[*].id' in runtime
    assert 'security_groups  = [aws_security_group.matrix.id]' in runtime
    assert 'target_group_arn = aws_lb_target_group.matrix_client[0].arn' in runtime
    assert 'container_port   = 8008' in runtime
    assert 'target_type = "ip"' in dns_tls


def test_synapse_runtime_injects_secret_handles_without_committed_secret_values():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")
    iam = read("infra/aws_baseline_80/iam.tf")

    assert 'aws_db_instance.matrix_synapse[0].master_user_secret[0].secret_arn' in runtime
    assert 'aws_secretsmanager_secret.matrix_homeserver_signing_key.arn' in runtime
    assert 'aws_secretsmanager_secret.matrix_macaroon_secret_key.arn' in runtime
    assert 'aws_secretsmanager_secret.matrix_registration_shared_secret.arn' in runtime
    assert 'aws_secretsmanager_secret.matrix_form_secret.arn' in runtime
    assert 'aws_db_instance.matrix_synapse[0].master_user_secret[0].secret_arn' in iam
    assert 'aws_secretsmanager_secret.matrix_homeserver_signing_key.arn' in iam
    assert 'secret_string' not in runtime


def test_synapse_database_and_media_have_deletion_and_backup_guards():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")
    backup = read("infra/aws_baseline_80/matrix_backup.tf")

    assert 'storage_encrypted     = true' in runtime
    assert 'deletion_protection       = var.matrix_synapse_deletion_protection' in runtime
    assert 'skip_final_snapshot       = false' in runtime
    assert 'prevent_destroy = true' in runtime
    assert 'aws_db_instance.matrix_synapse[0].arn' in backup
    assert 'aws_efs_file_system.matrix_synapse[0].arn' in backup
    assert 'multi_az               = var.matrix_synapse_postgres_multi_az' in runtime


def test_synapse_runtime_fails_closed_until_enabled_and_secrets_are_populated():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'count = var.enable_matrix_synapse ? 1 : 0' in runtime
    assert 'default     = false' in variables
    assert 'desired_count   = var.enable_matrix_synapse && var.start_ecs_services ? var.matrix_synapse_desired_count : 0' in runtime
    assert 'depends_on = [' in runtime
    assert 'aws_efs_mount_target.matrix_synapse' in runtime


def test_synapse_runtime_exposes_operator_outputs_and_example_inputs():
    outputs = read("infra/aws_baseline_80/outputs.tf")
    example = read("infra/aws_baseline_80/terraform.tfvars.example")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'output "matrix_synapse_service_name"' in outputs
    assert 'output "matrix_synapse_postgres_endpoint"' in outputs
    assert 'output "matrix_synapse_media_file_system_id"' in outputs
    assert 'enable_matrix_synapse = false' in example
    assert 'enable_matrix_backup  = false' in example
    assert 'matrix_synapse_image' in example
    assert 'matrixdotorg/synapse@sha256:' in variables
    assert 'matrixdotorg/synapse@sha256:' in example


def test_synapse_config_serializes_secret_values_without_yaml_interpolation():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")

    assert "yaml.safe_dump" in runtime
    assert 'os.environ["SYNAPSE_DB_PASSWORD"]' in runtime
    assert 'os.environ["SYNAPSE_MACAROON_SECRET_KEY"]' in runtime
    assert 'password: "$${SYNAPSE_DB_PASSWORD}"' not in runtime
    assert 'macaroon_secret_key: "$${SYNAPSE_MACAROON_SECRET_KEY}"' not in runtime


def test_federation_terminates_at_alb_without_direct_task_ingress():
    matrix = read("infra/aws_baseline_80/matrix_dns_tls.tf")
    edge = read("infra/aws_baseline_80/security_groups.tf")
    variables = read("infra/aws_baseline_80/variables.tf")
    runbook = read("docs/operations/matrix-production-evidence.md")

    assert 'description = "matrix_federation_8448_explicit"' not in matrix
    assert 'matrix_federation_allowed_ipv6_cidr_blocks' in variables
    assert 'var.enable_dual_stack_public_edge ? var.matrix_federation_allowed_ipv6_cidr_blocks : []' in edge
    assert 'https://synapse.zenith-research.ca:8448/_matrix/federation/v1/version' in runbook
