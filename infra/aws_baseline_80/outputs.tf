output "alb_dns_name" {
  description = "Public ALB DNS name."
  value       = aws_lb.gateway.dns_name
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

output "qdrant_api_key_secret_arn" {
  description = "Secrets Manager secret ARN for QDRANT_API_KEY."
  value       = aws_secretsmanager_secret.qdrant_api_key.arn
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

output "runtime_service_name" {
  description = "ECS service name for runtime-grpc."
  value       = aws_ecs_service.runtime.name
}

output "sandbox_service_name" {
  description = "ECS service name for tool-sandbox."
  value       = aws_ecs_service.sandbox.name
}

