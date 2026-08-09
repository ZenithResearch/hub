# Matrix/Synapse backup minimum for PRP-PR-014 / ISS-P14-005.
# This establishes an AWS Backup contract for the stateful Synapse resources that
# will be attached by resource ARN once the concrete EC2/EBS/RDS/media resources
# are accepted. It does not claim that a restore has been tested.

resource "aws_backup_vault" "matrix" {
  count = var.enable_matrix_backup ? 1 : 0

  name = "${local.name_prefix}-matrix-backup"
  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-backup" })
}

resource "aws_iam_role" "matrix_backup" {
  count = var.enable_matrix_backup ? 1 : 0

  name = "${local.name_prefix}-matrix-backup-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "backup.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "matrix_backup_service" {
  count = var.enable_matrix_backup ? 1 : 0

  role       = aws_iam_role.matrix_backup[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_iam_role_policy_attachment" "matrix_restore_service" {
  count = var.enable_matrix_backup ? 1 : 0

  role       = aws_iam_role.matrix_backup[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

resource "aws_backup_plan" "matrix" {
  count = var.enable_matrix_backup ? 1 : 0

  name = "${local.name_prefix}-matrix-backup-plan"

  rule {
    rule_name         = "daily-matrix-state"
    target_vault_name = aws_backup_vault.matrix[0].name
    schedule          = var.matrix_backup_schedule

    lifecycle {
      delete_after = var.matrix_backup_retention_days
    }
  }

  tags = local.tags
}

resource "aws_backup_selection" "matrix" {
  count = var.enable_matrix_backup && (var.enable_matrix_synapse || length(var.matrix_backup_resource_arns) > 0) ? 1 : 0

  iam_role_arn = aws_iam_role.matrix_backup[0].arn
  name         = "${local.name_prefix}-matrix-backup-selection"
  plan_id      = aws_backup_plan.matrix[0].id
  resources = concat(
    var.matrix_backup_resource_arns,
    var.enable_matrix_synapse ? [
      aws_db_instance.matrix_synapse[0].arn,
      aws_efs_file_system.matrix_synapse[0].arn,
    ] : [],
    var.enable_matrix_mas ? [
      aws_db_instance.matrix_mas[0].arn,
    ] : [],
  )
}
