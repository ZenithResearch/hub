from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IAC = ROOT / "infra/aws_baseline_80"


def test_cloud_agent_node_has_no_ingress_or_public_ip() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")

    security_group = terraform.split(
        'resource "aws_security_group" "hermes_cloud_agent" {', 1
    )[1].split('resource "aws_iam_role" "hermes_cloud_agent" {', 1)[0]
    instance = terraform.split('resource "aws_instance" "hermes_cloud_agent" {', 1)[1]

    assert "ingress {" not in security_group
    assert "associate_public_ip_address = false" in instance
    assert 'http_tokens   = "required"' in instance
    assert 'http_endpoint = "enabled"' in instance


def test_cloud_agent_uses_ssm_and_encrypted_persistent_storage() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")

    assert "AmazonSSMManagedInstanceCore" in terraform
    assert 'resource "aws_ebs_volume" "hermes_cloud_agent_state"' in terraform
    assert "encrypted         = true" in terraform
    assert 'resource "aws_volume_attachment" "hermes_cloud_agent_state"' in terraform
    assert 'device_name = "/dev/sdf"' in terraform


def test_cloud_agent_is_disabled_and_incomplete_configuration_fails_closed() -> None:
    variables = (IAC / "variables.tf").read_text(encoding="utf-8")
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    tfvars_example = (IAC / "terraform.tfvars.example").read_text(encoding="utf-8")

    assert 'variable "enable_hermes_cloud_agent"' in variables
    assert "default     = false" in variables
    assert 'variable "hermes_cloud_agent_ami_id"' in variables
    assert 'condition     = var.hermes_cloud_agent_ami_id != ""' in terraform
    assert 'variable "hermes_cloud_agent_secret_arns"' in variables
    assert "contains(var.hermes_cloud_agent_secret_arns" in terraform
    assert "var.hermes_cloud_agent_matrix_secret_arn" in terraform
    assert "count = var.enable_hermes_cloud_agent ? 1 : 0" in terraform
    assert "enable_hermes_cloud_agent = false" in tfvars_example


def test_cloud_agent_iam_secret_access_is_explicitly_scoped() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")

    assert 'actions   = ["secretsmanager:GetSecretValue"]' in terraform
    assert "resources = var.hermes_cloud_agent_secret_arns" in terraform
    assert 'actions   = ["kms:Decrypt"]' in terraform
    assert "resources = var.hermes_cloud_agent_secret_kms_key_arns" in terraform
    assert "secretsmanager:*" not in terraform
    assert 'resources = ["*"]' not in terraform


def test_cloud_agent_bootstrap_pins_hermes_and_materializes_profile_service() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    bootstrap = (IAC.parent / "hermes_cloud_agent/bootstrap.sh.tftpl").read_text(
        encoding="utf-8"
    )
    runner = (IAC.parent / "hermes_cloud_agent/runtime/hermes-cloud-agent-run").read_text(
        encoding="utf-8"
    )
    service = (
        IAC.parent / "hermes_cloud_agent/systemd/hermes-cloud-agent.service"
    ).read_text(encoding="utf-8")

    assert "user_data_base64" in terraform
    assert "templatefile(" in terraform
    assert "hermes_cloud_agent/bootstrap.sh.tftpl" in terraform
    assert "3ef6bbd201263d354fd83ec55b3c306ded2eb72a" in bootstrap
    assert 'install ".[matrix]"' in bootstrap
    assert "HERMES_HOME" in runner
    assert "MATRIX_E2EE_MODE=required" in runner
    assert "MATRIX_ALLOWED_USERS" in runner
    assert "MATRIX_ALLOWED_ROOMS" in runner
    assert "MATRIX_SESSION_SCOPE=room" in runner
    assert "API_SERVER_ENABLED=false" in runner
    assert "WEBHOOK_ENABLED=false" in runner
    assert "TERMINAL_ENV=docker" in runner
    assert "gateway run --external-supervisor" in runner
    assert "ConditionPathExists=/var/lib/hermes/models/READY" in service
    assert "NoNewPrivileges=true" in service


def test_cloud_agent_runtime_fetches_secret_without_persisting_raw_value() -> None:
    runner = (IAC.parent / "hermes_cloud_agent/runtime/hermes-cloud-agent-run").read_text(
        encoding="utf-8"
    )
    secret_reader = (
        IAC.parent / "hermes_cloud_agent/runtime/hermes-read-matrix-secret"
    ).read_text(encoding="utf-8")

    assert "hermes-read-matrix-secret" in runner
    assert 'boto3.client("secretsmanager")' in secret_reader
    assert "get_secret_value(SecretId=secret_arn)" in secret_reader
    assert "SecretBinary" not in secret_reader
    assert "aws secretsmanager" not in runner
    assert "MATRIX_ACCESS_TOKEN" in runner
    assert "MATRIX_DEVICE_ID" in runner
    assert "/etc/hermes-cloud-agent/profile.json" in runner
    assert ">>" not in runner
    assert ".env" not in runner
    assert "set -x" not in runner


def test_cloud_agent_uses_rootless_podman_socket_for_docker_backend() -> None:
    bootstrap = (IAC.parent / "hermes_cloud_agent/bootstrap.sh.tftpl").read_text(
        encoding="utf-8"
    )
    runner = (IAC.parent / "hermes_cloud_agent/runtime/hermes-cloud-agent-run").read_text(
        encoding="utf-8"
    )
    podman_service = (
        IAC.parent / "hermes_cloud_agent/systemd/hermes-podman.service"
    ).read_text(encoding="utf-8")

    assert "podman" in bootstrap
    assert "DOCKER_HOST=unix:///run/hermes-podman/podman.sock" in runner
    assert "User=hermes" in podman_service
    assert "podman system service" in podman_service
    assert "Delegate=yes" in podman_service
    assert "usermod -aG docker" not in bootstrap
    assert "docker.sock" not in runner


def test_cloud_agent_state_mount_waits_for_declared_encrypted_volume() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    mount_script = (
        IAC.parent / "hermes_cloud_agent/runtime/hermes-state-volume-mount"
    ).read_text(encoding="utf-8")

    assert "availability_zone = aws_subnet.private[0].availability_zone" in terraform
    assert "state_volume_id" in terraform
    assert "nvme-Amazon_Elastic_Block_Store" in mount_script
    assert "blkid" in mount_script
    assert "mkfs.ext4" in mount_script
    assert "/var/lib/hermes" in mount_script
