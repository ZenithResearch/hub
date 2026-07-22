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
    assert "length(var.hermes_cloud_agent_secret_arns) > 0" in terraform
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
