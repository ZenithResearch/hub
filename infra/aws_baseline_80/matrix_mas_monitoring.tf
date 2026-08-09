# Production alarms for Matrix Authentication Service and its dedicated RDS.

resource "aws_cloudwatch_metric_alarm" "matrix_mas_healthy_hosts" {
  count = var.enable_matrix_mas_public_edge ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-mas-no-healthy-target"
  alarm_description   = "MAS ALB target group has no healthy target."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = var.start_matrix_mas_service ? "breaching" : "notBreaching"
  alarm_actions       = local.matrix_effective_alarm_actions
  ok_actions          = local.matrix_effective_alarm_actions

  dimensions = {
    LoadBalancer = aws_lb.gateway.arn_suffix
    TargetGroup  = aws_lb_target_group.matrix_mas[0].arn_suffix
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_mas_5xx" {
  count = var.enable_matrix_mas_public_edge ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-mas-5xx"
  alarm_description   = "MAS target responses contain server errors."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.matrix_effective_alarm_actions
  ok_actions          = local.matrix_effective_alarm_actions

  dimensions = {
    LoadBalancer = aws_lb.gateway.arn_suffix
    TargetGroup  = aws_lb_target_group.matrix_mas[0].arn_suffix
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_mas_cpu" {
  count = var.enable_matrix_mas_public_edge ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-mas-ecs-cpu-high"
  alarm_description   = "MAS ECS CPU is above 80 percent."
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  treat_missing_data  = var.start_matrix_mas_service ? "breaching" : "notBreaching"
  alarm_actions       = local.matrix_effective_alarm_actions
  ok_actions          = local.matrix_effective_alarm_actions

  dimensions = {
    ClusterName = aws_ecs_cluster.this.name
    ServiceName = aws_ecs_service.matrix_mas[0].name
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_mas_rds_cpu" {
  count = var.enable_matrix_mas ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-mas-rds-cpu-high"
  alarm_description   = "MAS RDS CPU is above 80 percent."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  treat_missing_data  = "breaching"
  alarm_actions       = local.matrix_effective_alarm_actions
  ok_actions          = local.matrix_effective_alarm_actions

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.matrix_mas[0].identifier
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_mas_rds_free_storage" {
  count = var.enable_matrix_mas ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-mas-rds-storage-low"
  alarm_description   = "MAS RDS free storage is below 5 GiB."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 5368709120
  treat_missing_data  = "breaching"
  alarm_actions       = local.matrix_effective_alarm_actions
  ok_actions          = local.matrix_effective_alarm_actions

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.matrix_mas[0].identifier
  }

  tags = local.tags
}
