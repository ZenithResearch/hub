variable "aws_region" {
  description = "AWS region in which the standalone homeserver is created."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "This module is intentionally restricted to us-east-1."
  }
}

variable "ami_id" {
  description = "Explicit, reviewed Amazon Linux 2023 x86_64 AMI ID for us-east-1."
  type        = string

  validation {
    condition     = startswith(var.ami_id, "ami-")
    error_message = "ami_id must be an explicit EC2 AMI ID."
  }
}

variable "matrix_server_name" {
  description = "Public DNS hostname for Synapse and Caddy TLS (for example matrix.example.com)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$", var.matrix_server_name))
    error_message = "matrix_server_name must be a lowercase fully-qualified DNS hostname."
  }
}

variable "enable_runtime" {
  description = "Create the EC2 runtime only after the module-managed secret has a populated AWSCURRENT version."
  type        = bool
  default     = false
}

variable "instance_type" {
  description = "EC2 instance type for the standalone homeserver."
  type        = string
  default     = "t3.small"
}

variable "vpc_cidr" {
  description = "CIDR for the dedicated VPC."
  type        = string
  default     = "10.10.0.0/16"
}

variable "data_volume_size_gb" {
  description = "Size of the encrypted delete-on-termination data volume protected by the bootstrap DLM policy."
  type        = number
  default     = 30

  validation {
    condition     = var.data_volume_size_gb >= 20
    error_message = "data_volume_size_gb must be at least 20 GiB."
  }
}

variable "synapse_image" {
  description = "Immutable Synapse container image."
  type        = string
  default     = "matrixdotorg/synapse@sha256:6882d26594b87171e0fe807ac6bd7f0000665cd70e73fb88c58ec9bff14c19ce"
}

variable "postgres_image" {
  description = "Immutable PostgreSQL container image."
  type        = string
  default     = "postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297"
}

variable "caddy_image" {
  description = "Immutable Caddy container image."
  type        = string
  default     = "caddy:2.10.2-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d"
}

variable "admin_broker_image" {
  description = "Immutable reviewed Hypha administration broker container image."
  type        = string

  validation {
    condition     = can(regex("^[^[:space:]]+@sha256:[0-9a-f]{64}$", var.admin_broker_image))
    error_message = "admin_broker_image must be an immutable image reference containing @sha256:<64 lowercase hex>."
  }
}
