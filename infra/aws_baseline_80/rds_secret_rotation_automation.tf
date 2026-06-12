data "archive_file" "redeploy_gateway_after_clients_secret_rotation" {
  count = var.enable_clients_postgres ? 1 : 0

  type        = "zip"
  source_file = "${path.module}/lambda/redeploy_gateway_after_secret_rotation.py"
  output_path = "${path.module}/.terraform/redeploy_gateway_after_secret_rotation.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "redeploy_gateway_after_clients_secret_rotation" {
  count = var.enable_clients_postgres ? 1 : 0

  name               = "${local.name_prefix}-clients-secret-rotation-redeploy"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "redeploy_gateway_after_clients_secret_rotation_logs" {
  count = var.enable_clients_postgres ? 1 : 0

  role       = aws_iam_role.redeploy_gateway_after_clients_secret_rotation[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "redeploy_gateway_after_clients_secret_rotation" {
  count = var.enable_clients_postgres ? 1 : 0

  statement {
    actions = [
      "ecs:DescribeServices",
      "ecs:UpdateService",
    ]
    resources = [aws_ecs_service.gateway.id]
  }
}

resource "aws_iam_role_policy" "redeploy_gateway_after_clients_secret_rotation" {
  count = var.enable_clients_postgres ? 1 : 0

  name   = "${local.name_prefix}-clients-secret-rotation-redeploy"
  role   = aws_iam_role.redeploy_gateway_after_clients_secret_rotation[0].id
  policy = data.aws_iam_policy_document.redeploy_gateway_after_clients_secret_rotation[0].json
}

resource "aws_cloudwatch_log_group" "redeploy_gateway_after_clients_secret_rotation" {
  count = var.enable_clients_postgres ? 1 : 0

  name              = "/aws/lambda/${local.name_prefix}-clients-secret-rotation-redeploy"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_lambda_function" "redeploy_gateway_after_clients_secret_rotation" {
  count = var.enable_clients_postgres ? 1 : 0

  function_name    = "${local.name_prefix}-clients-secret-rotation-redeploy"
  description      = "Force a new gateway-http ECS deployment after the clients RDS managed secret rotates."
  role             = aws_iam_role.redeploy_gateway_after_clients_secret_rotation[0].arn
  handler          = "redeploy_gateway_after_secret_rotation.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.redeploy_gateway_after_clients_secret_rotation[0].output_path
  source_code_hash = data.archive_file.redeploy_gateway_after_clients_secret_rotation[0].output_base64sha256
  timeout          = 30

  environment {
    variables = {
      TARGET_SECRET_ARN = local.clients_postgres_secret_arn
      ECS_CLUSTER_ARN   = aws_ecs_cluster.this.arn
      ECS_SERVICE_NAME  = aws_ecs_service.gateway.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.redeploy_gateway_after_clients_secret_rotation,
    aws_iam_role_policy_attachment.redeploy_gateway_after_clients_secret_rotation_logs,
    aws_iam_role_policy.redeploy_gateway_after_clients_secret_rotation,
  ]

  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "clients_secret_rotation_redeploy_gateway" {
  count = var.enable_clients_postgres ? 1 : 0

  name        = "${local.name_prefix}-clients-secret-rotation-redeploy"
  description = "Redeploy gateway-http after the clients RDS managed secret rotates so ECS re-injects AWSCURRENT."

  event_pattern = jsonencode({
    source      = ["aws.secretsmanager"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["secretsmanager.amazonaws.com"]
      eventName   = ["RotationSucceeded", "UpdateSecretVersionStage"]
    }
  })

  tags = local.tags
}

resource "aws_cloudwatch_event_target" "clients_secret_rotation_redeploy_gateway" {
  count = var.enable_clients_postgres ? 1 : 0

  rule      = aws_cloudwatch_event_rule.clients_secret_rotation_redeploy_gateway[0].name
  target_id = "redeploy-gateway-http"
  arn       = aws_lambda_function.redeploy_gateway_after_clients_secret_rotation[0].arn
}

resource "aws_lambda_permission" "allow_clients_secret_rotation_eventbridge" {
  count = var.enable_clients_postgres ? 1 : 0

  statement_id  = "AllowExecutionFromClientsSecretRotationEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.redeploy_gateway_after_clients_secret_rotation[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.clients_secret_rotation_redeploy_gateway[0].arn
}
