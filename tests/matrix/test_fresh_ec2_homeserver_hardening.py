from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AWS = ROOT / "infra/matrix/aws"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_provider_is_locked_to_fresh_hypha_account_and_region():
    main = read("infra/matrix/aws/main.tf")
    variables = read("infra/matrix/aws/variables.tf")

    assert 'allowed_account_ids = ["610992396917"]' in main
    assert 'default     = "us-east-1"' in variables
    assert 'validation {' in variables
    assert 'var.aws_region == "us-east-1"' in variables


def test_public_edge_is_https_only_and_administration_is_ssm_only():
    main = read("infra/matrix/aws/main.tf")
    variables = read("infra/matrix/aws/variables.tf")

    assert 'from_port   = 80' in main
    assert 'from_port   = 443' in main
    for forbidden in [
        'from_port   = 22',
        'from_port   = 8008',
        'from_port   = 8448',
        'key_name',
        'ssh_cidr_blocks',
    ]:
        assert forbidden not in main
        assert forbidden not in variables
    assert 'AmazonSSMManagedInstanceCore' in main
    assert 'resource "aws_iam_instance_profile" "matrix"' in main
    assert 'associate_public_ip_address = true' in main


def test_secret_values_never_enter_terraform_or_user_data():
    variables = read("infra/matrix/aws/variables.tf")
    main = read("infra/matrix/aws/main.tf")
    user_data = read("infra/matrix/aws/user_data.sh.tpl")
    example = read("infra/matrix/aws/terraform.tfvars.example")

    for forbidden_variable in [
        'variable "matrix_db_password"',
        'variable "matrix_registration_secret"',
        'variable "matrix_macaroon_secret"',
        'variable "matrix_form_secret"',
    ]:
        assert forbidden_variable not in variables
    for forbidden in [
        "matrix_db_password",
        "matrix_registration_secret",
        "matrix_macaroon_secret",
        "matrix_form_secret",
        "CHANGE_ME",
    ]:
        assert forbidden not in main
        assert forbidden not in example
    assert 'resource "aws_secretsmanager_secret" "matrix"' in main
    assert 'aws_secretsmanager_secret.matrix.arn' in main
    assert "get-secret-value" in user_data
    assert "SecretString" in user_data
    assert 'chmod 600 /opt/matrix/.env' in user_data


def test_runtime_is_gated_on_populated_secret_and_uses_immutable_inputs():
    main = read("infra/matrix/aws/main.tf")
    variables = read("infra/matrix/aws/variables.tf")
    user_data = read("infra/matrix/aws/user_data.sh.tpl")

    assert 'data "aws_secretsmanager_secret_version" "matrix"' in main
    assert "var.enable_runtime" in main
    assert 'variable "ami_id"' in variables
    assert 'startswith(var.ami_id, "ami-")' in variables
    assert 'variable "synapse_image"' in variables
    assert 'variable "postgres_image"' in variables
    assert 'variable "caddy_image"' in variables
    assert variables.count("@sha256:") >= 3
    assert ":latest" not in user_data
    assert "most_recent = true" not in main


def test_data_volume_is_encrypted_disposable_and_mounted_by_nitro_id_and_uuid():
    main = read("infra/matrix/aws/main.tf")
    user_data = read("infra/matrix/aws/user_data.sh.tpl")

    assert 'ebs_block_device {' in main
    assert 'encrypted             = true' in main
    assert 'delete_on_termination = true' in main
    assert "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_" in user_data
    assert "timeout 120" in user_data
    assert "describe-volumes" in user_data
    assert "hypha-fresh-synapse-data" in user_data
    assert "EXPECTED_VOLUME_ID" in user_data
    assert "hypha-matrix-data" in user_data
    assert 'FILESYSTEM_TYPE="xfs"' in user_data
    assert 'FILESYSTEM_LABEL="hypha-matrix-data"' in user_data
    assert 'grep -Fq "UUID=$DATA_UUID " /etc/fstab' in user_data
    assert "findmnt" in user_data
    assert 'blkid -s UUID -o value' in user_data
    assert 'UUID=$DATA_UUID' in user_data
    assert "/dev/xvdf" not in user_data


def test_caddy_is_the_only_host_port_owner_and_synapse_uses_native_password_auth():
    user_data = read("infra/matrix/aws/user_data.sh.tpl")

    assert '"80:80"' in user_data
    assert '"443:443"' in user_data
    assert '"8008:8008"' not in user_data
    assert '"8448:8448"' not in user_data
    assert "reverse_proxy matrix-synapse:8008" in user_data
    assert "password_config:" in user_data
    assert "  enabled: true" in user_data
    assert "enable_password_config" not in user_data
    assert "serve_server_wellknown: true" in user_data
    assert "enable_registration: false" in user_data
    for forbidden in ["matrix-authentication-service", "msc4108", "delegated_auth"]:
        assert forbidden not in user_data.lower()


def test_outputs_expose_https_hostname_and_no_plaintext_backend_urls():
    main = read("infra/matrix/aws/main.tf")
    outputs = read("infra/matrix/aws/outputs.tf")

    assert 'matrix_public_url_json  = jsonencode("https://${var.matrix_server_name}/")' in main
    assert 'value       = "https://${var.matrix_server_name}"' in outputs
    assert "http://${aws_eip" not in outputs
    assert ":8008" not in outputs
    assert ":8448" not in outputs
    assert 'output "elastic_ip"' in outputs
    assert 'output "instance_id"' in outputs
