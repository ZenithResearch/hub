variable "project_name" {
  description = "Prefix for named AWS resources."
  type        = string
  default     = "agent-platform"
}

variable "environment" {
  description = "Environment name (e.g., dev/stage/prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region for regional resources (ECS/ALB/VPC/WAF regional)."
  type        = string
  default     = "us-west-2"
}

variable "vpc_cidr" {
  description = "VPC CIDR."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to use (2 is recommended)."
  type        = number
  default     = 2
}

variable "alb_idle_timeout_seconds" {
  description = "ALB idle timeout in seconds. Increase for long-lived SSE/WebSockets."
  type        = number
  default     = 3600
}

variable "enable_https" {
  description = "If true, create HTTPS listener (443) and redirect HTTP->HTTPS."
  type        = bool
  default     = true
}

variable "acm_cert_arn" {
  description = "ACM certificate ARN in the same region as the ALB (required if enable_https=true)."
  type        = string
  default     = ""

  validation {
    condition     = (!var.enable_https) || (trim(var.acm_cert_arn) != "")
    error_message = "acm_cert_arn must be set when enable_https=true."
  }
}

variable "enable_cloudfront" {
  description = "If true, create CloudFront distribution in front of ALB and a WAF ACL (CLOUDFRONT scope)."
  type        = bool
  default     = false
}

variable "cloudfront_acm_cert_arn" {
  description = "ACM certificate ARN in us-east-1 for CloudFront (required if enable_cloudfront=true and using custom domain)."
  type        = string
  default     = ""

  validation {
    condition = (
      (!var.enable_cloudfront) ||
      (var.domain_name == "") ||
      (var.domain_name != "" && trim(var.cloudfront_acm_cert_arn) != "")
    )
    error_message = "cloudfront_acm_cert_arn must be set when enable_cloudfront=true and domain_name is set."
  }
}

variable "domain_name" {
  description = "Optional DNS name to create (Route53 A/AAAA alias) pointing to CloudFront (if enabled) else ALB."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Route53 hosted zone id for domain_name."
  type        = string
  default     = ""

  validation {
    condition     = (var.domain_name == "") || (trim(var.route53_zone_id) != "")
    error_message = "route53_zone_id must be set when domain_name is set."
  }
}

variable "container_image" {
  description = "Container image URI (e.g., ECR image) used by all services (gateway/runtime/tool sandbox)."
  type        = string
}

variable "qdrant_url" {
  description = "Qdrant endpoint for runtime and indexer (e.g., Qdrant Cloud HTTPS URL)."
  type        = string
}

variable "qdrant_api_key_secret_arn" {
  description = "Optional Secrets Manager secret ARN whose value is the Qdrant API key. If empty, QDRANT_API_KEY is unset."
  type        = string
  default     = ""
}

variable "gateway_desired_count" {
  description = "Desired task count for gateway-http."
  type        = number
  default     = 2
}

variable "runtime_desired_count" {
  description = "Desired task count for runtime-grpc."
  type        = number
  default     = 2
}

variable "tool_desired_count" {
  description = "Desired task count for tool-sandbox."
  type        = number
  default     = 2
}

variable "task_cpu" {
  description = "Fargate task CPU units (applies to all services in this baseline)."
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory (MiB) (applies to all services in this baseline)."
  type        = number
  default     = 1024
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 14
}

variable "allow_cloudfront_only" {
  description = "If enable_cloudfront=true and this is true, restrict ALB ingress to CloudFront origin-facing managed prefix list."
  type        = bool
  default     = true
}

variable "cors_allow_origins" {
  description = "CORS_ALLOW_ORIGINS for the gateway."
  type        = string
  default     = "https://example.com"
}

variable "max_body_bytes" {
  description = "MAX_BODY_BYTES for gateway HTTP."
  type        = number
  default     = 262144
}

variable "tool_dir" {
  description = "Directory containing tool manifests inside the container."
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
  description = "Deterministic embedding vector dim (must match seeded collection)."
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
