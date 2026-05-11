locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = {
    Project     = "Agent Platform"
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  cloudmap_namespace = "${local.name_prefix}.local"
  runtime_target     = "runtime-grpc.${local.cloudmap_namespace}:50051"
  sandbox_target     = "tool-sandbox.${local.cloudmap_namespace}:50052"
  queue_grpc_target  = "queue.${local.cloudmap_namespace}:50053"
  queue_http_target  = "queue.${local.cloudmap_namespace}:8081"
}

