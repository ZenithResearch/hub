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


# ISS-P14-004: Synapse secret boundary (refs only, no raw values)
resource "aws_secretsmanager_secret" "synapse_registration_shared_secret" {
  name        = "${local.name_prefix}/synapse_registration_shared_secret"
  description = "Synapse registration_shared_secret (operator managed via Secrets Manager)"
  tags        = local.tags
}

resource "aws_secretsmanager_secret" "synapse_appservice_token" {
  name        = "${local.name_prefix}/synapse_appservice_token"
  description = "Synapse appservice token (operator managed)"
  tags        = local.tags
}

# ISS-P14-004 scope baseline: files confirmed: secrets.tf, variables.tf, user_data.sh.tpl, .env examples; current: secrets use var.* with sensitive=true, no raw values committed
# feat impl: use secret ARNs for matrix bootstrap in production
