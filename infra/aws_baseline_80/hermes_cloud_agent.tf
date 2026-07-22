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
    resources = var.hermes_cloud_agent_secret_arns
  }

  dynamic "statement" {
    for_each = length(var.hermes_cloud_agent_secret_kms_key_arns) > 0 ? [1] : []

    content {
      sid       = "DecryptDeclaredRuntimeSecrets"
      effect    = "Allow"
      actions   = ["kms:Decrypt"]
      resources = var.hermes_cloud_agent_secret_kms_key_arns
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
      condition     = length(var.hermes_cloud_agent_secret_arns) > 0
      error_message = "At least one declared runtime secret ARN is required when the Hermes cloud agent is enabled."
    }
  }

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-hermes-cloud-agent"
    Role = "matrix-only-hermes-agent"
  })
}

resource "aws_ebs_volume" "hermes_cloud_agent_state" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  availability_zone = aws_instance.hermes_cloud_agent[0].availability_zone
  encrypted         = true
  kms_key_id        = var.hermes_cloud_agent_state_kms_key_arn != "" ? var.hermes_cloud_agent_state_kms_key_arn : null
  size              = var.hermes_cloud_agent_state_volume_size_gib
  type              = "gp3"

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
