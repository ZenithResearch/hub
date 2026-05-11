output "alb_dns_name" {
  value       = aws_lb.gateway.dns_name
  description = "Public ALB DNS name (origin for CloudFront if enabled)."
}

output "cloudfront_domain_name" {
  value       = var.enable_cloudfront ? aws_cloudfront_distribution.this[0].domain_name : null
  description = "CloudFront distribution domain name (if enabled)."
}

output "gateway_public_url" {
  value = (
    var.domain_name != "" ? "https://${var.domain_name}" :
    (var.enable_cloudfront ? "https://${aws_cloudfront_distribution.this[0].domain_name}" :
    (var.enable_https ? "https://${aws_lb.gateway.dns_name}" : "http://${aws_lb.gateway.dns_name}"))
  )
  description = "Best public URL for gateway-http."
}

output "cloudmap_namespace" {
  value       = aws_service_discovery_private_dns_namespace.this.name
  description = "Private DNS namespace for service discovery."
}

output "runtime_grpc_target" {
  value       = local.runtime_grpc_target
  description = "Runtime gRPC DNS target (private)."
}

output "tool_sandbox_grpc_target" {
  value       = local.tool_grpc_target
  description = "Tool sandbox gRPC DNS target (private)."
}

output "ecs_cluster_name" {
  value       = aws_ecs_cluster.this.name
  description = "ECS cluster name."
}

output "kb_indexer_task_definition_arn" {
  value       = aws_ecs_task_definition.kb_indexer.arn
  description = "Task definition ARN for one-shot kb-indexer seeding task."
}

output "private_subnet_ids" {
  value       = aws_subnet.private[*].id
  description = "Private subnet ids (for ECS run-task networking)."
}

output "kb_indexer_security_group_id" {
  value       = aws_security_group.runtime.id
  description = "Security group id recommended for kb-indexer (needs Qdrant egress)."
}

