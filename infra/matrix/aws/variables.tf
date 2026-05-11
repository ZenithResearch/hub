variable "project_name" {
  description = "Project name prefix for all resources"
  type        = string
  default     = "hub"
}

variable "environment" {
  description = "Deployment environment (prod, staging)"
  type        = string
  default     = "prod"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

# Networking — defaults assume an existing VPC; can be left null to create new
variable "vpc_id" {
  description = "Existing VPC ID. Leave null to create a minimal VPC."
  type        = string
  default     = null
}

variable "subnet_id" {
  description = "Subnet ID for the EC2 instance (should be public if federation is enabled)"
  type        = string
  default     = null
}

variable "vpc_cidr" {
  description = "CIDR block for a new VPC (used only when vpc_id is null)"
  type        = string
  default     = "10.10.0.0/16"
}

# EC2
variable "instance_type" {
  description = "EC2 instance type. t3.small sufficient for a single-hub Matrix server."
  type        = string
  default     = "t3.small"
}

variable "key_name" {
  description = "EC2 key pair name for SSH access (leave null to disable SSH)"
  type        = string
  default     = null
}

variable "ebs_size_gb" {
  description = "Size of the data EBS volume in GB (media store + Postgres)"
  type        = number
  default     = 30
}

# Matrix config
variable "matrix_server_name" {
  description = "Matrix server name (e.g. matrix.yourdomain.com)"
  type        = string
}

variable "matrix_db_password" {
  description = "Postgres password for the synapse user"
  type        = string
  sensitive   = true
}

variable "matrix_registration_secret" {
  description = "Synapse registration shared secret (openssl rand -hex 32)"
  type        = string
  sensitive   = true
}

variable "matrix_macaroon_secret" {
  description = "Synapse macaroon secret (openssl rand -hex 32)"
  type        = string
  sensitive   = true
}

variable "matrix_form_secret" {
  description = "Synapse form secret (openssl rand -hex 32)"
  type        = string
  sensitive   = true
}

variable "matrix_federation_enabled" {
  description = "Enable Matrix federation (hub-to-hub messaging across Zenith network)"
  type        = bool
  default     = true
}

variable "matrix_enable_registration" {
  description = "Allow public registration (keep false — use admin API)"
  type        = bool
  default     = false
}

# Access
variable "allowed_cidr_blocks" {
  description = "CIDRs allowed to reach port 8008/8448. Defaults to open (use behind ALB or VPN in prod)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "ssh_cidr_blocks" {
  description = "CIDRs allowed SSH access (port 22). Empty list disables SSH ingress."
  type        = list(string)
  default     = []
}
