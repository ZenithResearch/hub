locals {
  hermes_cloud_agent_profile_home = "/var/lib/hermes/profiles/${var.hermes_cloud_agent_profile_id}"
  local_inference_lock            = jsondecode(file("${path.module}/../hermes_cloud_agent/artifacts/local-inference.lock.json"))
  local_inference_lock_sha256     = filesha256("${path.module}/../hermes_cloud_agent/artifacts/local-inference.lock.json")
  hermes_cloud_agent_profile_contract = {
    schema_version = 1
    profile = {
      id   = var.hermes_cloud_agent_profile_id
      home = local.hermes_cloud_agent_profile_home
    }
    matrix = {
      homeserver              = var.hermes_cloud_agent_matrix_homeserver
      user_id                 = var.hermes_cloud_agent_matrix_user_id
      access_token_secret_ref = "aws-secretsmanager:${var.hermes_cloud_agent_matrix_secret_arn}"
      crypto_store            = "${local.hermes_cloud_agent_profile_home}/platforms/matrix/store"
      e2ee_mode               = "required"
      allowed_users           = var.hermes_cloud_agent_matrix_allowed_users
      allowed_rooms           = var.hermes_cloud_agent_matrix_allowed_rooms
      session_scope           = "room"
    }
    gateway = {
      api_server_enabled = false
    }
    inference = {
      provider             = "custom"
      base_url             = "http://127.0.0.1:8080/v1"
      model_id             = local.local_inference_lock.desired.model.model_id
      model_sha256         = local.local_inference_lock.desired.model.sha256
      artifact_lock_sha256 = local.local_inference_lock_sha256
      fallbacks            = []
    }
    sandbox = {
      backend                = "docker"
      network                = false
      host_mounts            = false
      credential_passthrough = false
      allowed_toolsets       = ["clarify", "file", "memory", "terminal", "todo"]
    }
    storage = {
      encrypted = true
    }
    operations = {
      administration       = "ssm"
      public_ssh           = false
      public_agent_ingress = false
    }
  }
  hermes_cloud_agent_config = {
    agent = {
      disabled_toolsets = [
        "browser",
        "code_execution",
        "computer_use",
        "cronjob",
        "delegation",
        "homeassistant",
        "messaging",
        "skills",
      ]
    }
    group_sessions_per_user = true
    platform_toolsets = {
      matrix = ["clarify", "file", "memory", "terminal", "todo"]
    }
    terminal = {
      credential_files              = []
      docker_env                    = {}
      docker_forward_env            = []
      docker_mount_cwd_to_workspace = false
      docker_network                = false
      docker_volumes                = []
      env_type                      = "docker"
    }
    security = {
      redact_secrets = true
    }
  }
}

data "aws_kms_key" "hermes_cloud_agent_state" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  key_id = var.hermes_cloud_agent_state_kms_key_arn
}

resource "aws_security_group" "hermes_cloud_agent" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  name        = "${local.name_prefix}-hermes-cloud-agent"
  description = "Matrix-only Hermes agent: outbound sync/model administration, no ingress"
  vpc_id      = aws_vpc.this.id

  egress {
    description = "HTTPS for Matrix sync, SSM, Secrets Manager, and pinned artifact retrieval"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "VPC DNS over UDP"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "VPC DNS over TCP"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-hermes-cloud-agent"
  })
}

data "aws_iam_policy_document" "hermes_cloud_agent_assume_role" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "hermes_cloud_agent" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  name               = "${local.name_prefix}-hermes-cloud-agent"
  assume_role_policy = data.aws_iam_policy_document.hermes_cloud_agent_assume_role[0].json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "hermes_cloud_agent_ssm" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  role       = aws_iam_role.hermes_cloud_agent[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "hermes_cloud_agent_secrets" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  statement {
    sid       = "ReadDeclaredRuntimeSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.hermes_cloud_agent_matrix_secret_arn]
  }

  dynamic "statement" {
    for_each = length(var.hermes_cloud_agent_secret_kms_key_arns) > 0 ? [1] : []

    content {
      sid       = "DecryptDeclaredRuntimeSecrets"
      effect    = "Allow"
      actions   = ["kms:Decrypt"]
      resources = var.hermes_cloud_agent_secret_kms_key_arns

      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
      }

      condition {
        test     = "StringEquals"
        variable = "kms:EncryptionContext:SecretARN"
        values   = [var.hermes_cloud_agent_matrix_secret_arn]
      }
    }
  }
}

resource "aws_iam_role_policy" "hermes_cloud_agent_secrets" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  name   = "${local.name_prefix}-hermes-cloud-agent-secrets"
  role   = aws_iam_role.hermes_cloud_agent[0].id
  policy = data.aws_iam_policy_document.hermes_cloud_agent_secrets[0].json
}

resource "aws_iam_instance_profile" "hermes_cloud_agent" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  name = "${local.name_prefix}-hermes-cloud-agent"
  role = aws_iam_role.hermes_cloud_agent[0].name
  tags = local.tags
}

resource "aws_instance" "hermes_cloud_agent" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  ami                         = var.hermes_cloud_agent_ami_id
  instance_type               = var.hermes_cloud_agent_instance_type
  subnet_id                   = aws_subnet.private[0].id
  vpc_security_group_ids      = [aws_security_group.hermes_cloud_agent[0].id]
  associate_public_ip_address = false
  iam_instance_profile        = aws_iam_instance_profile.hermes_cloud_agent[0].name
  user_data_replace_on_change = true
  user_data_base64 = base64encode(templatefile("${path.module}/../hermes_cloud_agent/bootstrap.sh.tftpl", {
    profile_json_b64       = base64encode(jsonencode(local.hermes_cloud_agent_profile_contract))
    profile_schema_b64     = filebase64("${path.module}/../hermes_cloud_agent/profile.schema.json")
    profile_config_b64     = base64encode(yamlencode(local.hermes_cloud_agent_config))
    state_volume_id        = aws_ebs_volume.hermes_cloud_agent_state[0].id
    runner_b64             = filebase64("${path.module}/../hermes_cloud_agent/runtime/hermes-cloud-agent-run")
    mount_script_b64       = filebase64("${path.module}/../hermes_cloud_agent/runtime/hermes-state-volume-mount")
    control_helper_b64     = filebase64("${path.module}/../hermes_cloud_agent/runtime/hermes-cloud-agent-control")
    secret_reader_b64      = filebase64("${path.module}/../hermes_cloud_agent/runtime/hermes-read-matrix-secret")
    matrix_trust_patch_b64 = filebase64("${path.module}/../hermes_cloud_agent/patches/strict-matrix-device-trust.patch")
    state_service_b64      = filebase64("${path.module}/../hermes_cloud_agent/systemd/hermes-state-volume.service")
    podman_service_b64     = filebase64("${path.module}/../hermes_cloud_agent/systemd/hermes-podman.service")
    gateway_service_b64    = filebase64("${path.module}/../hermes_cloud_agent/systemd/hermes-cloud-agent.service")
  }))

  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = var.hermes_cloud_agent_root_volume_size_gib
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  lifecycle {
    precondition {
      condition     = var.hermes_cloud_agent_ami_id != ""
      error_message = "hermes_cloud_agent_ami_id must be set when enable_hermes_cloud_agent is true."
    }

    precondition {
      condition     = var.hermes_cloud_agent_matrix_secret_arn != ""
      error_message = "The Matrix secret ARN must be set when the agent is enabled."
    }

    precondition {
      condition     = can(regex("^@[^: ]+:[^ ]+$", var.hermes_cloud_agent_matrix_user_id))
      error_message = "A dedicated Matrix user ID is required when the Hermes cloud agent is enabled."
    }

    precondition {
      condition     = length(var.hermes_cloud_agent_matrix_allowed_users) > 0 && length(var.hermes_cloud_agent_matrix_allowed_rooms) > 0
      error_message = "Non-empty Matrix user and room allowlists are required when the Hermes cloud agent is enabled."
    }

    precondition {
      condition = (
        can(regex("^[a-f0-9]{64}$", local.local_inference_lock.desired.llama_cpp.archive_sha256)) &&
        can(regex("^[a-f0-9]{64}$", local.local_inference_lock.desired.model.sha256)) &&
        local.local_inference_lock.desired.llama_cpp.s3_version_id != "" &&
        local.local_inference_lock.desired.llama_cpp.s3_version_id != "null" &&
        local.local_inference_lock.desired.model.s3_version_id != "" &&
        local.local_inference_lock.desired.model.s3_version_id != "null"
      )
      error_message = "Exact versioned runtime and model artifacts with pinned SHA-256 digests are required when the Hermes cloud agent is enabled."
    }

    precondition {
      condition     = var.hermes_cloud_agent_state_kms_key_arn != ""
      error_message = "A dedicated customer-managed KMS key is required for Hermes Matrix state."
    }

    precondition {
      condition = (
        data.aws_kms_key.hermes_cloud_agent_state[0].key_manager == "CUSTOMER" &&
        data.aws_kms_key.hermes_cloud_agent_state[0].enabled &&
        startswith(data.aws_kms_key.hermes_cloud_agent_state[0].arn, "arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:key/")
      )
      error_message = "Hermes Matrix state requires an enabled customer-managed KMS key in this account and region."
    }
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-hermes-cloud-agent"
    Role = "matrix-only-hermes-agent"
  })
}

resource "aws_ebs_volume" "hermes_cloud_agent_state" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  availability_zone    = aws_subnet.private[0].availability_zone
  encrypted            = true
  kms_key_id           = var.hermes_cloud_agent_state_kms_key_arn
  multi_attach_enabled = false
  size                 = var.hermes_cloud_agent_state_volume_size_gib
  type                 = "gp3"

  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-hermes-cloud-agent-state"
    Role = "hermes-profile-matrix-crypto-model-state"
  })
}

resource "aws_volume_attachment" "hermes_cloud_agent_state" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.hermes_cloud_agent_state[0].id
  instance_id = aws_instance.hermes_cloud_agent[0].id
}

resource "aws_ssm_document" "hermes_cloud_agent_control" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  name          = "${local.name_prefix}-hermes-agent-control"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Bounded lifecycle control for the Matrix-only Hermes agent"
    parameters = {
      Action = {
        type          = "String"
        allowedValues = ["enable", "disable", "restart", "status"]
      }
    }
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "ControlHermesAgent"
        inputs = {
          runCommand = [
            "/usr/local/libexec/hermes-cloud-agent-control '{{ Action }}'",
          ]
        }
      },
    ]
  })

  tags = local.tags
}
