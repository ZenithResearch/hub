terraform {
  required_version = "= 1.14.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 5.100.0"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = ["610992396917"]

  default_tags {
    tags = {
      Project   = "hypha"
      Component = "fresh-synapse"
      ManagedBy = "terraform"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "matrix" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "hypha-fresh-synapse" }
}

resource "aws_internet_gateway" "matrix" {
  vpc_id = aws_vpc.matrix.id

  tags = { Name = "hypha-fresh-synapse" }
}

resource "aws_subnet" "matrix" {
  vpc_id                  = aws_vpc.matrix.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 0)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = { Name = "hypha-fresh-synapse" }
}

resource "aws_route_table" "matrix" {
  vpc_id = aws_vpc.matrix.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.matrix.id
  }

  tags = { Name = "hypha-fresh-synapse" }
}

resource "aws_route_table_association" "matrix" {
  subnet_id      = aws_subnet.matrix.id
  route_table_id = aws_route_table.matrix.id
}

resource "aws_security_group" "matrix" {
  name        = "hypha-fresh-synapse"
  description = "Public Caddy TLS edge only"
  vpc_id      = aws_vpc.matrix.id

  ingress {
    description = "Caddy HTTP challenge and HTTPS redirect"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Caddy HTTPS Matrix client and federation edge"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Package, image, AWS API, DNS, and ACME access"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_secretsmanager_secret" "matrix" {
  name                    = "hypha/fresh-synapse/runtime"
  description             = "Runtime-only Synapse and PostgreSQL credentials"
  recovery_window_in_days = 7
}

# This intentionally disabled declaration records the prohibited provider path:
# enabling it would copy SecretString into Terraform state. Runtime gating is
# instead enforced by the instance bootstrap's AWSCURRENT fetch and exact-key
# validation, while enable_runtime remains the operator's explicit creation gate.
data "aws_secretsmanager_secret_version" "matrix" {
  count     = 0
  secret_id = aws_secretsmanager_secret.matrix.id
}

resource "aws_iam_role" "matrix" {
  name                 = "hypha-fresh-synapse"
  permissions_boundary = "arn:aws:iam::610992396917:policy/HyphaSynapseInstanceBoundary"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.matrix.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "matrix_secret" {
  name = "read-exact-matrix-secret"
  role = aws_iam_role.matrix.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.matrix.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ec2:DescribeVolumes"]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "matrix" {
  name = "hypha-fresh-synapse"
  role = aws_iam_role.matrix.name
}

resource "aws_instance" "matrix" {
  count = var.enable_runtime ? 1 : 0

  ami                         = var.ami_id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.matrix.id
  vpc_security_group_ids      = [aws_security_group.matrix.id]
  iam_instance_profile        = aws_iam_instance_profile.matrix.name
  associate_public_ip_address = true
  user_data_replace_on_change = true

  # No secret-version data is referenced: that AWS provider data source would
  # persist SecretString in state. Bootstrap fetches AWSCURRENT at runtime.
  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    secret_arn              = aws_secretsmanager_secret.matrix.arn
    aws_region              = var.aws_region
    matrix_server_name      = var.matrix_server_name
    matrix_server_name_json = jsonencode(var.matrix_server_name)
    matrix_public_url_json  = jsonencode("https://${var.matrix_server_name}/")
    synapse_image           = var.synapse_image
    postgres_image          = var.postgres_image
    caddy_image             = var.caddy_image
  })

  # User data is first-boot input. Automatic replacement is unsafe while the
  # persistent Matrix EBS block is inline and delete-on-termination. Reconcile
  # later boot-policy changes through SSM and controlled-reboot acceptance.
  lifecycle {
    ignore_changes = [user_data]
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
  }

  ebs_block_device {
    device_name           = "/dev/sdf"
    volume_type           = "gp3"
    volume_size           = var.data_volume_size_gb
    encrypted             = true
    delete_on_termination = true
    tags = {
      Name = "hypha-fresh-synapse-data"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.ssm,
    aws_iam_role_policy.matrix_secret,
  ]

  tags = { Name = "hypha-fresh-synapse" }
}

resource "aws_eip" "matrix" {
  count = var.enable_runtime ? 1 : 0

  instance = aws_instance.matrix[0].id
  domain   = "vpc"

  tags = { Name = "hypha-fresh-synapse" }
}
