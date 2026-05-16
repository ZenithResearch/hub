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

resource "aws_secretsmanager_secret" "review_access_admin_token" {
  name        = "${local.name_prefix}/review_access_admin_token"
  description = "Zenith Hub operator Review Access rotation bearer token"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "review_access_admin_token" {
  count = var.review_access_admin_token != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.review_access_admin_token.id
  secret_string = var.review_access_admin_token
}

