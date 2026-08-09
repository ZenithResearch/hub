# Matrix/Synapse secret handles for PRP-PR-014 / ISS-P14-004.
# These resources define secret names/handles only. Raw production values must be
# supplied by the operator through approved secret input channels and must never
# be committed to source.

resource "aws_secretsmanager_secret" "matrix_homeserver_signing_key" {
  name        = "${local.name_prefix}/matrix/homeserver_signing_key"
  description = "Synapse homeserver signing key material for the production Matrix host"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "matrix_homeserver_signing_key" {
  count = var.matrix_homeserver_signing_key != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.matrix_homeserver_signing_key.id
  secret_string = var.matrix_homeserver_signing_key
}

resource "aws_secretsmanager_secret" "matrix_macaroon_secret_key" {
  name        = "${local.name_prefix}/matrix/macaroon_secret_key"
  description = "Synapse macaroon secret key"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "matrix_macaroon_secret_key" {
  count = var.matrix_macaroon_secret_key != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.matrix_macaroon_secret_key.id
  secret_string = var.matrix_macaroon_secret_key
}

resource "aws_secretsmanager_secret" "matrix_registration_shared_secret" {
  name        = "${local.name_prefix}/matrix/registration_shared_secret"
  description = "Synapse controlled-provisioning registration shared secret"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "matrix_registration_shared_secret" {
  count = var.matrix_registration_shared_secret != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.matrix_registration_shared_secret.id
  secret_string = var.matrix_registration_shared_secret
}

resource "aws_secretsmanager_secret" "matrix_form_secret" {
  name        = "${local.name_prefix}/matrix/form_secret"
  description = "Synapse form secret"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "matrix_form_secret" {
  count = var.matrix_form_secret != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.matrix_form_secret.id
  secret_string = var.matrix_form_secret
}

resource "aws_secretsmanager_secret" "matrix_mas_synapse_shared_secret" {
  name        = "${local.name_prefix}/matrix/mas_synapse_shared_secret"
  description = "Shared secret authenticating Synapse and Matrix Authentication Service"
  tags        = local.tags
}

resource "aws_secretsmanager_secret" "matrix_mas_encryption_secret" {
  name        = "${local.name_prefix}/matrix/mas_encryption_secret"
  description = "Immutable 32-byte hex-encoded MAS field and cookie encryption key"
  tags        = local.tags
}

resource "aws_secretsmanager_secret" "matrix_mas_signing_key" {
  name        = "${local.name_prefix}/matrix/mas_signing_key"
  description = "Stable RSA private signing key for MAS OAuth and OIDC tokens"
  tags        = local.tags
}

resource "aws_secretsmanager_secret" "matrix_appservice_as_token" {
  name        = "${local.name_prefix}/matrix/appservice_as_token"
  description = "Synapse appservice as_token for Hub Matrix appservice"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "matrix_appservice_as_token" {
  count = var.matrix_appservice_as_token != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.matrix_appservice_as_token.id
  secret_string = var.matrix_appservice_as_token
}

resource "aws_secretsmanager_secret" "matrix_appservice_hs_token" {
  name        = "${local.name_prefix}/matrix/appservice_hs_token"
  description = "Synapse appservice hs_token for Hub Matrix appservice"
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "matrix_appservice_hs_token" {
  count = var.matrix_appservice_hs_token != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.matrix_appservice_hs_token.id
  secret_string = var.matrix_appservice_hs_token
}
