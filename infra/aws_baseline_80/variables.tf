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

variable "enable_dual_stack_public_edge" {
  description = "Enable IPv6 on the public ALB edge and public subnets. Private ECS tasks remain private."
  type        = bool
  default     = true
}

variable "public_hub_domain_name" {
  description = "Optional public DNS name for the gateway ALB HTTPS certificate, e.g. hub.zenith-research.ca. Leave empty for HTTP-only bootstrap."
  type        = string
  default     = ""
}

variable "enable_https_listener" {
  description = "Create the ALB HTTPS listener and redirect HTTP to HTTPS. Enable only after the ACM DNS validation record has propagated and the certificate is issued."
  type        = bool
  default     = false
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
  description = "Default Fargate memory in MiB for low-footprint services. Individual services may override this."
  type        = number
  default     = 512
}

variable "gateway_task_cpu" {
  description = "Fargate CPU units for gateway-http. Defaults to the baseline task_cpu."
  type        = number
  default     = 256
}

variable "gateway_task_memory" {
  description = "Fargate memory in MiB for gateway-http. Defaults to the baseline task_memory."
  type        = number
  default     = 512
}

variable "runtime_task_cpu" {
  description = "Fargate CPU units for runtime-grpc."
  type        = number
  default     = 256
}

variable "runtime_task_memory" {
  description = "Fargate memory in MiB for runtime-grpc."
  type        = number
  default     = 512
}

variable "sandbox_task_cpu" {
  description = "Fargate CPU units for tool-sandbox."
  type        = number
  default     = 256
}

variable "sandbox_task_memory" {
  description = "Fargate memory in MiB for tool-sandbox."
  type        = number
  default     = 512
}

variable "queue_task_cpu" {
  description = "Fargate CPU units for queue."
  type        = number
  default     = 256
}

variable "queue_task_memory" {
  description = "Fargate memory in MiB for queue."
  type        = number
  default     = 512
}

variable "cases_task_cpu" {
  description = "Fargate CPU units for cases."
  type        = number
  default     = 256
}

variable "cases_task_memory" {
  description = "Fargate memory in MiB for cases."
  type        = number
  default     = 512
}

variable "eventbus_task_cpu" {
  description = "Fargate CPU units for eventbus."
  type        = number
  default     = 256
}

variable "eventbus_task_memory" {
  description = "Fargate memory in MiB for eventbus."
  type        = number
  default     = 512
}

variable "frank_task_cpu" {
  description = "Fargate CPU units for Frank dispatcher."
  type        = number
  default     = 256
}

variable "frank_task_memory" {
  description = "Fargate memory in MiB for Frank dispatcher."
  type        = number
  default     = 512
}

variable "stt_http_task_cpu" {
  description = "Fargate CPU units for STT HTTP. Must stay >=1024 in prod; 256/512 caused Whisper OOM."
  type        = number
  default     = 1024
}

variable "stt_http_task_memory" {
  description = "Fargate memory in MiB for STT HTTP. Must stay >=2048 in prod; 256/512 caused Whisper OOM."
  type        = number
  default     = 2048
}

variable "llama_server_task_cpu" {
  description = "Fargate CPU units for internal llama.cpp server. Production Qwen GGUF service uses 4096."
  type        = number
  default     = 4096
}

variable "llama_server_task_memory" {
  description = "Fargate memory in MiB for internal llama.cpp server. Production Qwen GGUF service uses 16384."
  type        = number
  default     = 16384
}

variable "llama_server_image" {
  description = "Container image for llama.cpp OpenAI-compatible server. Do not bake multi-GB GGUFs into local-built images."
  type        = string
  default     = "ghcr.io/ggml-org/llama.cpp:server"
}

variable "llama_server_model_name" {
  description = "GGUF model filename served by internal llama-server."
  type        = string
  default     = "Qwen3.5-9B-Q4_K_M.gguf"
}

variable "llama_server_model_bucket_name" {
  description = "Private S3 bucket containing staged llama-server models. Empty derives the production-style bucket name from name_prefix/account/region."
  type        = string
  default     = ""
}

variable "llama_server_model_s3_key" {
  description = "S3 key for the staged llama-server GGUF model."
  type        = string
  default     = "models/Qwen3.5-9B-Q4_K_M.gguf"
}

variable "llama_server_model_expected_sha256" {
  description = "Optional SHA256 expected for the staged llama-server GGUF model. Empty skips checksum enforcement in the preload task."
  type        = string
  default     = ""
}

variable "llama_model_preload_image" {
  description = "AWS CLI image used by the one-shot model preload task. This downloads from S3 into EFS; it must not bake model bytes into the image."
  type        = string
  default     = "public.ecr.aws/aws-cli/aws-cli:latest"
}

variable "start_ecs_services" {
  description = "When false, ECS services are created with desired_count=0 so infrastructure/ECR/cert/storage can be applied before images exist. Set true after images are pushed."
  type        = bool
  default     = true
}

variable "gateway_desired_count" {
  description = "Desired task count for gateway-http when start_ecs_services is true. Keep at 1 while CLIENTS_DB_PATH uses SQLite; Postgres-backed deployments may raise this later."
  type        = number
  default     = 1
}

variable "gateway_clients_db_path" {
  description = "Persistent clients registry path for gateway-http Review SDK auth."
  type        = string
  default     = "/data/clients.db"
}

variable "gateway_reviews_data_dir" {
  description = "Writable reviews data directory for gateway-http review assets/submissions."
  type        = string
  default     = "/data/reviews"
}

variable "gateway_model_profiles_path" {
  description = "Canonical static model profile contract path mounted in gateway-http."
  type        = string
  default     = "infra/model-profiles.yaml"
}

variable "gateway_model_profile_overrides_path" {
  description = "Writable model profile runtime override path on gateway EFS."
  type        = string
  default     = "/data/model-profile-overrides.yaml"
}

variable "gateway_model_profile_audit_path" {
  description = "Writable model profile audit JSONL path on gateway EFS."
  type        = string
  default     = "/data/model-profile-audit.jsonl"
}

variable "gateway_data_uid" {
  description = "POSIX uid used by the gateway container app user for the EFS access point."
  type        = number
  default     = 100
}

variable "gateway_data_gid" {
  description = "POSIX gid used by the gateway container app group for the EFS access point."
  type        = number
  default     = 101
}

variable "enable_clients_postgres" {
  description = "Create a private RDS Postgres database for the gateway Review SDK clients registry and configure gateway-http to use it."
  type        = bool
  default     = false
}

variable "clients_postgres_database_name" {
  description = "Database name for the clients registry Postgres database."
  type        = string
  default     = "hub_clients"
}

variable "clients_postgres_username" {
  description = "Master username for the clients registry Postgres database. Password is generated by RDS managed Secrets Manager."
  type        = string
  default     = "hub_clients"
}

variable "clients_postgres_instance_class" {
  description = "RDS instance class for clients registry Postgres."
  type        = string
  default     = "db.t4g.micro"
}

variable "clients_postgres_engine_version" {
  description = "Postgres engine version for clients registry RDS."
  type        = string
  default     = "16.6"
}

variable "clients_postgres_allocated_storage_gb" {
  description = "Initial allocated storage in GiB for clients registry RDS."
  type        = number
  default     = 20
}

variable "clients_postgres_max_allocated_storage_gb" {
  description = "Maximum autoscaled storage in GiB for clients registry RDS."
  type        = number
  default     = 100
}

variable "clients_postgres_backup_retention_days" {
  description = "Backup retention in days for clients registry RDS."
  type        = number
  default     = 7
}

variable "clients_postgres_backup_window" {
  description = "Preferred backup window for clients registry RDS."
  type        = string
  default     = "08:00-09:00"
}

variable "clients_postgres_maintenance_window" {
  description = "Preferred maintenance window for clients registry RDS."
  type        = string
  default     = "sun:09:00-sun:10:00"
}

variable "clients_postgres_deletion_protection" {
  description = "Enable deletion protection for clients registry RDS."
  type        = bool
  default     = true
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

# Cases must stay at 1 while SQLite is backed by EFS.
variable "cases_desired_count" {
  description = "Desired task count for cases (must be 1 — SQLite single-writer constraint)."
  type        = number
  default     = 1
}

variable "eventbus_desired_count" {
  description = "Desired task count for eventbus."
  type        = number
  default     = 1
}

variable "frank_desired_count" {
  description = "Desired task count for Frank dispatcher."
  type        = number
  default     = 1
}

variable "frank_model" {
  description = "Model name Frank uses for model-backed paths. Keep aligned with the internal llama-server served model."
  type        = string
  default     = "Qwen3.5-9B-Q4_K_M.gguf"
}

variable "frank_openai_base_url" {
  description = "OpenAI-compatible base URL used by Frank model-backed paths. Production points at the private internal llama-server."
  type        = string
  default     = "http://llama-server.zenith-hub-prod.local:3690/v1"
}

variable "stt_http_desired_count" {
  description = "Desired task count for the STT HTTP service. Keep at 1 while managed STT fallback is needed."
  type        = number
  default     = 1
}

variable "stt_provider" {
  description = "Frank STT provider: local_whisper or elevenlabs."
  type        = string
  default     = "local_whisper"
}

variable "stt_model" {
  description = "Selected STT model for the provider. For ElevenLabs use scribe_v2."
  type        = string
  default     = "scribe_v2"
}

variable "stt_fallback_provider" {
  description = "Optional fallback STT provider after primary provider failures."
  type        = string
  default     = "local_whisper"
}

variable "stt_audio_preprocessor" {
  description = "Optional pre-STT audio processor: none or elevenlabs_audio_isolation. Keep none for the baseline rollout."
  type        = string
  default     = "none"
}

variable "elevenlabs_api_key_secret_arn" {
  description = "AWS Secrets Manager or SSM secret ARN containing ELEVENLABS_API_KEY. Empty disables secret injection."
  type        = string
  default     = ""
}

variable "llama_server_desired_count" {
  description = "Desired task count for internal llama-server. Keep at 1 unless model memory/concurrency is redesigned."
  type        = number
  default     = 1
}

variable "image_tag" {
  description = "Default image tag pushed to the ECR repos for each service."
  type        = string
  default     = "latest"
}

variable "gateway_image_tag" {
  description = "Optional gateway-http image tag override. Use when gateway has been hotfix-deployed ahead of the shared image_tag."
  type        = string
  default     = ""
}

variable "eventbus_image_tag" {
  description = "Optional eventbus image tag override. Use to preserve a live eventbus revision when rolling gateway-http only."
  type        = string
  default     = ""
}

variable "cases_image_tag" {
  description = "Optional cases service image tag override. Use when cases has been hotfix-deployed ahead of gateway-http."
  type        = string
  default     = ""
}

variable "frank_image_tag" {
  description = "Optional Frank service image tag override. Use when Frank has been hotfix-deployed ahead of gateway-http."
  type        = string
  default     = ""
}

variable "stt_image_tag" {
  description = "Optional STT HTTP image tag override. The image is stored in the gateway ECR repository until a dedicated STT repo is needed."
  type        = string
  default     = ""
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

variable "review_access_admin_token" {
  description = "Bearer token for operator-only Review Access rotation API. If set, it will be stored in Terraform state; prefer setting the Secrets Manager value out-of-band for production."
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

variable "enable_matrix_backup" {
  description = "Create the Matrix/Synapse AWS Backup vault and plan. Resource selection remains empty until concrete Matrix state resource ARNs are reviewed."
  type        = bool
  default     = false
}

variable "enable_matrix_synapse" {
  description = "Create and run the production Synapse ECS/RDS/EFS target. Keep false until required Matrix secret values exist and an operator accepts the plan."
  type        = bool
  default     = false
}

variable "matrix_synapse_image" {
  description = "Pinned upstream Synapse container image. Update only through a reviewed production change."
  type        = string
  default     = "matrixdotorg/synapse:v1.132.0"
}

variable "matrix_synapse_desired_count" {
  description = "Desired Synapse task count when enable_matrix_synapse is true. The initial production topology is intentionally single-worker."
  type        = number
  default     = 1
}

variable "matrix_synapse_task_cpu" {
  description = "Fargate CPU units for Synapse."
  type        = number
  default     = 512
}

variable "matrix_synapse_task_memory" {
  description = "Fargate memory in MiB for Synapse."
  type        = number
  default     = 1024
}

variable "matrix_synapse_postgres_instance_class" {
  description = "RDS instance class for Synapse Postgres."
  type        = string
  default     = "db.t4g.micro"
}

variable "matrix_synapse_postgres_engine_version" {
  description = "Postgres engine version for Synapse RDS."
  type        = string
  default     = "16.6"
}

variable "matrix_synapse_postgres_allocated_storage_gb" {
  description = "Initial allocated storage in GiB for Synapse RDS."
  type        = number
  default     = 20
}

variable "matrix_synapse_postgres_max_allocated_storage_gb" {
  description = "Maximum autoscaled storage in GiB for Synapse RDS."
  type        = number
  default     = 100
}

variable "matrix_synapse_backup_retention_days" {
  description = "Automated RDS backup retention for Synapse."
  type        = number
  default     = 7
}

variable "matrix_synapse_deletion_protection" {
  description = "Protect the production Synapse RDS instance from deletion."
  type        = bool
  default     = true
}

variable "matrix_backup_schedule" {
  description = "AWS Backup cron expression for Matrix/Synapse state backups."
  type        = string
  default     = "cron(0 9 * * ? *)"
}

variable "matrix_backup_retention_days" {
  description = "Retention in days for Matrix/Synapse backup recovery points."
  type        = number
  default     = 35
}

variable "matrix_backup_resource_arns" {
  description = "Stateful Matrix/Synapse resource ARNs selected for backup: DB, media volume/storage, signing-key/config storage. Empty creates no selection."
  type        = list(string)
  default     = []
}

variable "matrix_homeserver_signing_key" {
  description = "Sensitive Synapse homeserver signing key material. Empty means do not write/update the secret version from Terraform."
  type        = string
  default     = ""
  sensitive   = true
}

variable "matrix_macaroon_secret_key" {
  description = "Sensitive Synapse macaroon secret key. Empty means do not write/update the secret version from Terraform."
  type        = string
  default     = ""
  sensitive   = true
}

variable "matrix_registration_shared_secret" {
  description = "Sensitive Synapse registration shared secret for controlled provisioning. Empty means do not write/update the secret version from Terraform."
  type        = string
  default     = ""
  sensitive   = true
}

variable "matrix_appservice_as_token" {
  description = "Sensitive Matrix appservice as_token. Empty means do not write/update the secret version from Terraform."
  type        = string
  default     = ""
  sensitive   = true
}

variable "matrix_appservice_hs_token" {
  description = "Sensitive Matrix appservice hs_token. Empty means do not write/update the secret version from Terraform."
  type        = string
  default     = ""
  sensitive   = true
}

variable "public_matrix_domain_name" {
  description = "Public Matrix/Synapse server_name and client API host. Locked v0 target: synapse.zenith-research.ca. Empty disables public Matrix DNS/TLS resources."
  type        = string
  default     = "synapse.zenith-research.ca"
}

variable "matrix_hosted_zone_id" {
  description = "Route53 hosted zone ID that owns public_matrix_domain_name. Empty disables Route53 records and ACM DNS validation records."
  type        = string
  default     = ""
}

variable "enable_matrix_https_listener" {
  description = "Enable the ALB HTTPS listener for Matrix client API after ACM validation and target readiness are confirmed."
  type        = bool
  default     = false
}

variable "matrix_https_listener_rule_priority" {
  description = "Priority for the Matrix host-header rule on the existing Hub HTTPS ALB listener."
  type        = number
  default     = 110
}

variable "enable_matrix_federation" {
  description = "Intentionally expose Matrix federation on 8448. Keep false until the security group, listener, and smoke checks are reviewed."
  type        = bool
  default     = false
}

variable "matrix_federation_allowed_cidr_blocks" {
  description = "CIDR allowlist for Matrix federation ingress on 8448 when enable_matrix_federation is true."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
