data "aws_iam_policy_document" "task_execution_secrets" {
  count = var.qdrant_api_key_secret_arn != "" ? 1 : 0

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.qdrant_api_key_secret_arn]
  }
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  count  = var.qdrant_api_key_secret_arn != "" ? 1 : 0
  name   = "${local.name_prefix}-ecs-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_secrets[0].json
}

