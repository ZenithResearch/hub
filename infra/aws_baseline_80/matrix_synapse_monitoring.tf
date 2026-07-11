# Production alarms for the single-worker Synapse v0 topology. Starting the
# service is fail-closed unless matrix_alarm_actions has an incident destination.

resource "aws_cloudwatch_metric_alarm" "matrix_synapse_healthy_hosts" {
  count = var.enable_matrix_synapse ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-no-healthy-target"
  alarm_description   = "Matrix ALB target group has no healthy Synapse target."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = local.matrix_effective_alarm_actions
  ok_actions          = local.matrix_effective_alarm_actions

  dimensions = {
    LoadBalancer = aws_lb.gateway.arn_suffix
    TargetGroup  = aws_lb_target_group.matrix_client[0].arn_suffix
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_synapse_cpu" {
  count = var.enable_matrix_synapse ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-ecs-cpu-high"
  alarm_description   = "Synapse ECS CPU is above 80 percent."
  namespace           = "AWS/ECS"
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
    ClusterName = aws_ecs_cluster.this.name
    ServiceName = aws_ecs_service.matrix_synapse[0].name
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_synapse_memory" {
  count = var.enable_matrix_synapse ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-ecs-memory-high"
  alarm_description   = "Synapse ECS memory is above 80 percent."
  namespace           = "AWS/ECS"
  metric_name         = "MemoryUtilization"
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
    ClusterName = aws_ecs_cluster.this.name
    ServiceName = aws_ecs_service.matrix_synapse[0].name
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_synapse_rds_cpu" {
  count = var.enable_matrix_synapse ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-rds-cpu-high"
  alarm_description   = "Synapse RDS CPU is above 80 percent."
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
    DBInstanceIdentifier = aws_db_instance.matrix_synapse[0].identifier
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_synapse_rds_free_storage" {
  count = var.enable_matrix_synapse ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-rds-storage-low"
  alarm_description   = "Synapse RDS free storage is below 5 GiB."
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
    DBInstanceIdentifier = aws_db_instance.matrix_synapse[0].identifier
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_synapse_rds_connections" {
  count = var.enable_matrix_synapse ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-rds-connections-high"
  alarm_description   = "Synapse RDS connections exceed the accepted single-worker envelope."
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 70
  treat_missing_data  = "breaching"
  alarm_actions       = local.matrix_effective_alarm_actions
  ok_actions          = local.matrix_effective_alarm_actions

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.matrix_synapse[0].identifier
  }

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "matrix_synapse_efs_burst_credits" {
  count = var.enable_matrix_synapse ? 1 : 0

  alarm_name          = "${local.name_prefix}-matrix-efs-burst-credits-low"
  alarm_description   = "Synapse EFS burst credits are below 1 GiB."
  namespace           = "AWS/EFS"
  metric_name         = "BurstCreditBalance"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 1073741824
  treat_missing_data  = "breaching"
  alarm_actions       = local.matrix_effective_alarm_actions
  ok_actions          = local.matrix_effective_alarm_actions

  dimensions = {
    FileSystemId = aws_efs_file_system.matrix_synapse[0].id
  }

  tags = local.tags
}
