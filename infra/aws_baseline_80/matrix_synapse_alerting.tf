locals {
  matrix_effective_alarm_actions = concat(
    var.matrix_alarm_actions,
    var.enable_matrix_synapse ? [aws_sns_topic.matrix_alerts[0].arn] : [],
  )
}

resource "aws_sns_topic" "matrix_alerts" {
  count = var.enable_matrix_synapse ? 1 : 0

  name              = "${local.name_prefix}-matrix-production-alerts"
  kms_master_key_id = "alias/aws/sns"

  tags = local.tags
}

resource "aws_sns_topic_subscription" "matrix_alert_email" {
  count = var.enable_matrix_synapse && var.matrix_alarm_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.matrix_alerts[0].arn
  protocol  = "email"
  endpoint  = var.matrix_alarm_email
}
