resource "aws_secretsmanager_secret" "qdrant_api_key" {
  name        = "${local.name_prefix}/qdrant_api_key"
  description = "Agent Platform: Qdrant API key used by runtime-grpc"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "qdrant_api_key" {
  count = var.qdrant_api_key != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.qdrant_api_key.id
  secret_string = var.qdrant_api_key
}

