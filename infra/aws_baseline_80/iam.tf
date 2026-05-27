data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name_prefix}-ecs-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Secret injection uses the *execution* role.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [
        aws_secretsmanager_secret.qdrant_api_key.arn,
        aws_secretsmanager_secret.review_access_admin_token.arn,
      ],
      var.enable_clients_postgres ? [aws_db_instance.clients[0].master_user_secret[0].secret_arn] : []
    )
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${local.name_prefix}-execution-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "gateway_task" {
  name               = "${local.name_prefix}-gateway-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role" "runtime_task" {
  name               = "${local.name_prefix}-runtime-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role" "sandbox_task" {
  name               = "${local.name_prefix}-sandbox-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role" "queue_task" {
  name               = "${local.name_prefix}-queue-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role" "cases_task" {
  name               = "${local.name_prefix}-cases-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role" "frank_task" {
  name               = "${local.name_prefix}-frank-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role" "stt_http_task" {
  name               = "${local.name_prefix}-stt-http-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role" "llama_server_task" {
  name               = "${local.name_prefix}-llama-server-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

# Queue task needs elasticfilesystem:ClientMount + ClientWrite to mount EFS.
data "aws_iam_policy_document" "queue_efs" {
  statement {
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
      "elasticfilesystem:ClientRootAccess",
    ]
    resources = [aws_efs_file_system.queue.arn]
  }
}

resource "aws_iam_role_policy" "queue_efs" {
  name   = "${local.name_prefix}-queue-efs"
  role   = aws_iam_role.queue_task.id
  policy = data.aws_iam_policy_document.queue_efs.json
}

# Cases task needs elasticfilesystem:ClientMount + ClientWrite to mount EFS.
data "aws_iam_policy_document" "cases_efs" {
  statement {
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
      "elasticfilesystem:ClientRootAccess",
    ]
    resources = [aws_efs_file_system.cases.arn]
  }
}

resource "aws_iam_role_policy" "cases_efs" {
  name   = "${local.name_prefix}-cases-efs"
  role   = aws_iam_role.cases_task.id
  policy = data.aws_iam_policy_document.cases_efs.json
}

# Frank task needs EFS access for execution artifacts.
data "aws_iam_policy_document" "frank_efs" {
  statement {
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
      "elasticfilesystem:ClientRootAccess",
    ]
    resources = [aws_efs_file_system.frank.arn]
  }
}

resource "aws_iam_role_policy" "frank_efs" {
  name   = "${local.name_prefix}-frank-efs"
  role   = aws_iam_role.frank_task.id
  policy = data.aws_iam_policy_document.frank_efs.json
}

# STT HTTP task mounts Frank execution artifacts read-only for transcription.
data "aws_iam_policy_document" "stt_http_efs" {
  statement {
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientRootAccess",
    ]
    resources = [aws_efs_file_system.frank.arn]
  }
}

resource "aws_iam_role_policy" "stt_http_efs" {
  name   = "${local.name_prefix}-stt-http-efs"
  role   = aws_iam_role.stt_http_task.id
  policy = data.aws_iam_policy_document.stt_http_efs.json
}

# Llama-server mounts Frank EFS read-only for the staged GGUF model and may read the
# private S3 source object during explicit model-staging/preload operations.
data "aws_iam_policy_document" "llama_server_model_efs" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.llama_server_model_bucket_name}/${var.llama_server_model_s3_key}"]
  }

  statement {
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.llama_server_model_bucket_name}"]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["models/*"]
    }
  }

  statement {
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
      "elasticfilesystem:ClientRootAccess",
    ]
    resources = [aws_efs_file_system.frank.arn]
  }
}

resource "aws_iam_role_policy" "llama_server_model_efs" {
  name   = "llama-server-model-efs"
  role   = aws_iam_role.llama_server_task.id
  policy = data.aws_iam_policy_document.llama_server_model_efs.json
}

# Gateway task mounts its own persistent CLIENTS_DB_PATH=/data/clients.db and reads
# Frank execution artifacts through /data/frank_execution for HubFS previews.
data "aws_iam_policy_document" "gateway_efs" {
  statement {
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
      "elasticfilesystem:ClientRootAccess",
    ]
    resources = [aws_efs_file_system.gateway.arn]
  }

  statement {
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientRootAccess",
    ]
    resources = [aws_efs_file_system.frank.arn]

    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.frank_execution.arn]
    }
  }
}

resource "aws_iam_role_policy" "gateway_efs" {
  name   = "${local.name_prefix}-gateway-efs"
  role   = aws_iam_role.gateway_task.id
  policy = data.aws_iam_policy_document.gateway_efs.json
}

# Least privilege baseline: task roles have no permissions by default.
# (Runtime reads QDRANT_API_KEY via env var injected by the ECS agent.)

