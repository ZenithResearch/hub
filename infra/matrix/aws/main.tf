terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  name_prefix = "${var.project_name}-matrix-${var.environment}"

  tags = {
    Project     = var.project_name
    Environment = var.environment
    Component   = "matrix"
    ManagedBy   = "terraform"
  }
}

# ──────────────────────────────────────────────
# Networking (optional — skips if vpc_id provided)
# ──────────────────────────────────────────────

resource "aws_vpc" "matrix" {
  count      = var.vpc_id == null ? 1 : 0
  cidr_block = var.vpc_cidr

  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "matrix" {
  count  = var.vpc_id == null ? 1 : 0
  vpc_id = aws_vpc.matrix[0].id

  tags = merge(local.tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_subnet" "matrix" {
  count                   = var.vpc_id == null ? 1 : 0
  vpc_id                  = aws_vpc.matrix[0].id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 1)
  map_public_ip_on_launch = true
  availability_zone       = data.aws_availability_zones.available.names[0]

  tags = merge(local.tags, { Name = "${local.name_prefix}-subnet" })
}

resource "aws_route_table" "matrix" {
  count  = var.vpc_id == null ? 1 : 0
  vpc_id = aws_vpc.matrix[0].id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.matrix[0].id
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-rt" })
}

resource "aws_route_table_association" "matrix" {
  count          = var.vpc_id == null ? 1 : 0
  subnet_id      = aws_subnet.matrix[0].id
  route_table_id = aws_route_table.matrix[0].id
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  resolved_vpc_id    = var.vpc_id != null ? var.vpc_id : aws_vpc.matrix[0].id
  resolved_subnet_id = var.subnet_id != null ? var.subnet_id : aws_subnet.matrix[0].id
}

# ──────────────────────────────────────────────
# Security Group
# ──────────────────────────────────────────────

resource "aws_security_group" "matrix" {
  name        = "${local.name_prefix}-sg"
  description = "Matrix Synapse — client (8008) and federation (8448)"
  vpc_id      = local.resolved_vpc_id

  # Client API
  ingress {
    from_port   = 8008
    to_port     = 8008
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
    description = "Matrix client API"
  }

  # Federation
  dynamic "ingress" {
    for_each = var.matrix_federation_enabled ? [1] : []
    content {
      from_port   = 8448
      to_port     = 8448
      protocol    = "tcp"
      cidr_blocks = var.allowed_cidr_blocks
      description = "Matrix federation"
    }
  }

  # SSH (optional)
  dynamic "ingress" {
    for_each = length(var.ssh_cidr_blocks) > 0 ? [1] : []
    content {
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.ssh_cidr_blocks
      description = "SSH access"
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-sg" })
}

# ──────────────────────────────────────────────
# EBS data volume (Postgres + media store)
# ──────────────────────────────────────────────

resource "aws_ebs_volume" "matrix_data" {
  availability_zone = data.aws_availability_zones.available.names[0]
  size              = var.ebs_size_gb
  type              = "gp3"
  encrypted         = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-data" })
}

resource "aws_volume_attachment" "matrix_data" {
  device_name  = "/dev/xvdf"
  volume_id    = aws_ebs_volume.matrix_data.id
  instance_id  = aws_instance.matrix.id
  force_detach = false
}

# ──────────────────────────────────────────────
# EC2 instance
# ──────────────────────────────────────────────

data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "matrix" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.instance_type
  subnet_id              = local.resolved_subnet_id
  vpc_security_group_ids = [aws_security_group.matrix.id]
  key_name               = var.key_name

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    delete_on_termination = true
    encrypted             = true
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tpl", {
    matrix_server_name         = var.matrix_server_name
    matrix_db_password         = var.matrix_db_password
    matrix_registration_secret = var.matrix_registration_secret
    matrix_macaroon_secret     = var.matrix_macaroon_secret
    matrix_form_secret         = var.matrix_form_secret
    matrix_federation_enabled  = tostring(var.matrix_federation_enabled)
    matrix_enable_registration = tostring(var.matrix_enable_registration)
  }))

  tags = merge(local.tags, { Name = "${local.name_prefix}-ec2" })
}

# Elastic IP for stable DNS target
resource "aws_eip" "matrix" {
  instance = aws_instance.matrix.id
  domain   = "vpc"

  tags = merge(local.tags, { Name = "${local.name_prefix}-eip" })
}
