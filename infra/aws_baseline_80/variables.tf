variable "project_name" {
  description = "Resource name prefix (keep short)."
  type        = string
  default     = "agent-platform"
}

variable "environment" {
  description = "Environment label (dev/stage/prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-west-2"
}

variable "vpc_cidr" {
  description = "VPC CIDR."
  type        = string
  default     = "10.20.0.0/16"
}

variable "alb_idle_timeout_seconds" {
  description = "ALB idle timeout (increase for WebSockets/SSE stability)."
  type        = number
  default     = 3600
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 14
}

# Target cost profile: 0.25 vCPU / 0.5 GB RAM always on, per service
variable "task_cpu" {
  description = "Fargate CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate memory in MiB (512 = 0.5 GB)."
  type        = number
  default     = 512
}

variable "gateway_desired_count" {
  description = "Desired task count for gateway-http."
  type        = number
  default     = 1
}

variable "runtime_desired_count" {
  description = "Desired task count for runtime-grpc."
  type        = number
  default     = 1
}

variable "sandbox_desired_count" {
  description = "Desired task count for tool-sandbox."
  type        = number
  default     = 1
}

# Queue must stay at 1 — SQLite is single-writer and the EFS access point is not
# safe for concurrent writers. Scale by increasing throughput, not replicas.
variable "queue_desired_count" {
  description = "Desired task count for queue (must be 1 — SQLite single-writer constraint)."
  type        = number
  default     = 1
}

variable "image_tag" {
  description = "Image tag pushed to the ECR repos for each service."
  type        = string
  default     = "latest"
}

variable "qdrant_url" {
  description = "External Qdrant endpoint URL (Qdrant Cloud)."
  type        = string
}

variable "qdrant_api_key" {
  description = "Qdrant API key. If set, it will be stored in Terraform state."
  type        = string
  sensitive   = true
  default     = ""
}

variable "cors_allow_origins" {
  description = "CORS_ALLOW_ORIGINS for gateway-http."
  type        = string
  default     = "https://example.com"
}

variable "max_body_bytes" {
  description = "MAX_BODY_BYTES for gateway-http."
  type        = number
  default     = 262144
}

variable "tool_dir" {
  description = "Tool manifest directory inside the container."
  type        = string
  default     = "/app/libs/tools"
}

variable "tool_default_timeout_ms" {
  description = "Default tool timeout (ms)."
  type        = number
  default     = 5000
}

variable "tool_default_max_memory_mb" {
  description = "Default tool max memory (MB)."
  type        = number
  default     = 128
}

variable "kb_vector_dim" {
  description = "KB vector dimension (must match existing seeded collection)."
  type        = number
  default     = 256
}

variable "qdrant_collection" {
  description = "Qdrant collection name for KB."
  type        = string
  default     = "kb_documents"
}

variable "enable_execute_command" {
  description = "Enable ECS Exec on services."
  type        = bool
  default     = false
}
