resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${local.name_prefix}/gateway-http"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/ecs/${local.name_prefix}/runtime-grpc"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "sandbox" {
  name              = "/ecs/${local.name_prefix}/tool-sandbox"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "queue" {
  name              = "/ecs/${local.name_prefix}/queue"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "cases" {
  name              = "/ecs/${local.name_prefix}/cases"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}


resource "aws_cloudwatch_log_group" "eventbus" {
  name              = "/ecs/${local.name_prefix}/eventbus"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "frank" {
  name              = "/ecs/${local.name_prefix}/frank"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "stt_http" {
  name              = "/ecs/${local.name_prefix}/stt-http"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}
