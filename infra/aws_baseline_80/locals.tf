locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = {
    Project     = "Agent Platform"
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  cloudmap_namespace  = "${local.name_prefix}.local"
  runtime_target      = "runtime-grpc.${local.cloudmap_namespace}:50051"
  sandbox_target      = "tool-sandbox.${local.cloudmap_namespace}:50052"
  queue_grpc_target   = "queue.${local.cloudmap_namespace}:50053"
  queue_http_target   = "queue.${local.cloudmap_namespace}:8081"
  cases_http_target   = "cases.${local.cloudmap_namespace}:8083"
  eventbus_target     = "eventbus.${local.cloudmap_namespace}:8082"
  stt_http_target     = "stt-http.${local.cloudmap_namespace}:8765"
  llama_server_target = "llama-server.${local.cloudmap_namespace}:3690"

  gateway_image_tag = var.gateway_image_tag != "" ? var.gateway_image_tag : var.image_tag
  cases_image_tag   = var.cases_image_tag != "" ? var.cases_image_tag : local.gateway_image_tag
  frank_image_tag   = var.frank_image_tag != "" ? var.frank_image_tag : local.gateway_image_tag
  stt_image_tag     = var.stt_image_tag != "" ? var.stt_image_tag : local.gateway_image_tag

  llama_server_model_bucket_name = var.llama_server_model_bucket_name != "" ? var.llama_server_model_bucket_name : "${local.name_prefix}-llama-models-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
}

