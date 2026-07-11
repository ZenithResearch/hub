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
    assert 'sslmode": "verify-full"' in runtime
    assert 'sslrootcert": "/data/aws-rds-global-bundle.pem"' in runtime
    assert 'matrix_rds_ca_bundle_sha256' in runtime


def test_synapse_runtime_fails_closed_until_enabled_and_secrets_are_populated():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'count = var.enable_matrix_synapse ? 1 : 0' in runtime
    assert 'default     = false' in variables
    assert 'var.enable_matrix_synapse && var.start_ecs_services && var.start_matrix_synapse_service' in runtime
    assert 'variable "start_matrix_synapse_service"' in variables
    assert 'depends_on = [' in runtime
    assert 'aws_efs_mount_target.matrix_synapse' in runtime
    assert 'var.enable_matrix_backup' in runtime
    assert 'var.matrix_synapse_desired_count == 1' in runtime
    assert 'var.matrix_alarm_email != "" || length(var.matrix_alarm_actions) > 0' in runtime
    assert 'var.matrix_synapse_image' in runtime and 'runtime-grpc@sha256:' in runtime
    assert 'matrix_synapse_image must be a digest-pinned hardened ECR image' in runtime


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
    assert 'dkr.ecr.us-east-1.amazonaws.com/zenith-hub-prod-runtime-grpc@sha256:' in example


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
    assert 'matrix_federation_outbound_8448' in matrix


def test_synapse_deployment_has_rollback_monitoring_and_capacity_guards():
    runtime = read("infra/aws_baseline_80/matrix_synapse_runtime.tf")
    monitoring = read("infra/aws_baseline_80/matrix_synapse_monitoring.tf")
    alerting = read("infra/aws_baseline_80/matrix_synapse_alerting.tf")
    variables = read("infra/aws_baseline_80/variables.tf")

    assert 'deployment_circuit_breaker' in runtime
    assert 'rollback = true' in runtime
    assert 'deployment_minimum_healthy_percent = 100' in runtime
    assert 'matrix_synapse_task_cpu' in variables and 'default     = 1024' in variables
    assert 'matrix_synapse_task_memory' in variables and 'default     = 2048' in variables
    assert 'default     = "db.t4g.small"' in variables
    for alarm in [
        "matrix_synapse_healthy_hosts",
        "matrix_synapse_cpu",
        "matrix_synapse_memory",
        "matrix_synapse_rds_cpu",
        "matrix_synapse_rds_free_storage",
        "matrix_synapse_rds_connections",
        "matrix_synapse_efs_burst_credits",
    ]:
        assert f'resource "aws_cloudwatch_metric_alarm" "{alarm}"' in monitoring
    assert 'alarm_actions' in monitoring and 'local.matrix_effective_alarm_actions' in monitoring
    assert 'resource "aws_sns_topic" "matrix_alerts"' in alerting
    assert 'resource "aws_sns_topic_subscription" "matrix_alert_email"' in alerting
    assert 'kms_master_key_id = "alias/aws/sns"' in alerting


def test_hardened_synapse_image_build_is_non_root_and_scanned():
    dockerfile = read("infra/matrix/synapse/Dockerfile")
    workflow = read(".github/workflows/synapse-image.yml")

    assert "matrixdotorg/synapse@sha256:" in dockerfile
    assert '"cryptography==48.0.1"' in dockerfile
    assert '"Twisted==26.4.0rc2"' in dockerfile
    assert "rm -f /usr/sbin/gosu" in dockerfile
    assert "USER 991:991" in dockerfile
    assert "aquasecurity/trivy-action@v0.36.0" in workflow
    assert "severity: HIGH,CRITICAL" in workflow
    assert 'exit-code: "1"' in workflow
