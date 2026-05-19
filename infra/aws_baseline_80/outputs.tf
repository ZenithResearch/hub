output "alb_dns_name" {
  description = "Public ALB DNS name."
  value       = aws_lb.gateway.dns_name
}

output "alb_zone_id" {
  description = "Public ALB canonical hosted zone ID."
  value       = aws_lb.gateway.zone_id
}

output "public_hub_url" {
  description = "Configured public Hub URL. HTTPS is active only after enable_https_listener is true and the certificate is issued."
  value       = var.public_hub_domain_name != "" ? (var.enable_https_listener ? "https://${var.public_hub_domain_name}" : "http://${var.public_hub_domain_name}") : null
}

output "gateway_certificate_dns_validation_records" {
  description = "DNS records to add at the DNS provider to validate the ACM certificate."
  value = var.public_hub_domain_name != "" ? [
    for option in aws_acm_certificate.gateway[0].domain_validation_options : {
      name  = option.resource_record_name
      type  = option.resource_record_type
      value = option.resource_record_value
    }
  ] : []
}

output "ecr_gateway_repo_url" {
  description = "ECR repo URL for gateway-http."
  value       = aws_ecr_repository.gateway.repository_url
}

output "ecr_runtime_repo_url" {
  description = "ECR repo URL for runtime-grpc."
  value       = aws_ecr_repository.runtime.repository_url
}

output "ecr_sandbox_repo_url" {
  description = "ECR repo URL for tool-sandbox."
  value       = aws_ecr_repository.sandbox.repository_url
}

output "ecr_queue_repo_url" {
  description = "ECR repo URL for queue."
  value       = aws_ecr_repository.queue.repository_url
}

output "qdrant_api_key_secret_arn" {
  description = "Secrets Manager secret ARN for QDRANT_API_KEY."
  value       = aws_secretsmanager_secret.qdrant_api_key.arn
}

output "review_access_admin_token_secret_arn" {
  description = "Secrets Manager secret ARN for REVIEW_ACCESS_ADMIN_TOKEN."
  value       = aws_secretsmanager_secret.review_access_admin_token.arn
}

output "cloudmap_namespace" {
  description = "Cloud Map private DNS namespace."
  value       = aws_service_discovery_private_dns_namespace.this.name
}

output "runtime_grpc_target" {
  description = "Internal DNS target for runtime gRPC."
  value       = local.runtime_target
}

output "tool_sandbox_grpc_target" {
  description = "Internal DNS target for tool-sandbox gRPC."
  value       = local.sandbox_target
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "gateway_service_name" {
  description = "ECS service name for gateway-http."
  value       = aws_ecs_service.gateway.name
}

output "gateway_clients_db_path" {
  description = "Persistent CLIENTS_DB_PATH mounted in gateway-http."
  value       = var.gateway_clients_db_path
}

output "clients_postgres_endpoint" {
  description = "Private RDS endpoint for the clients registry Postgres database."
  value       = var.enable_clients_postgres ? aws_db_instance.clients[0].endpoint : null
}

output "clients_postgres_secret_arn" {
  description = "Secrets Manager ARN for the generated clients registry Postgres master user secret."
  value       = var.enable_clients_postgres ? aws_db_instance.clients[0].master_user_secret[0].secret_arn : null
  sensitive   = true
}

output "runtime_service_name" {
  description = "ECS service name for runtime-grpc."
  value       = aws_ecs_service.runtime.name
}

output "sandbox_service_name" {
  description = "ECS service name for tool-sandbox."
  value       = aws_ecs_service.sandbox.name
}

output "queue_service_name" {
  description = "ECS service name for queue."
  value       = aws_ecs_service.queue.name
}

output "cases_service_name" {
  description = "ECS service name for cases."
  value       = aws_ecs_service.cases.name
}


output "eventbus_service_name" {
  description = "ECS service name for eventbus."
  value       = aws_ecs_service.eventbus.name
}

output "frank_service_name" {
  description = "ECS service name for Frank dispatcher."
  value       = aws_ecs_service.frank.name
}

output "stt_http_service_name" {
  description = "ECS service name for STT HTTP."
  value       = aws_ecs_service.stt_http.name
}


output "llama_server_service_name" {
  description = "ECS service name for internal llama-server."
  value       = aws_ecs_service.llama_server.name
}

output "llama_server_openai_target" {
  description = "Internal OpenAI-compatible llama-server target."
  value       = "http://${local.llama_server_target}/v1"
}
