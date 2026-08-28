# Optional autoscaling stubs. They are disabled until a deployment profile defines
# measurable scaling targets, limits, and verification evidence.
#
# resource "aws_appautoscaling_target" "gateway" {
#   max_capacity       = 4
#   min_capacity       = 1
#   resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.gateway.name}"
#   scalable_dimension = "ecs:service:DesiredCount"
#   service_namespace  = "ecs"
# }
#
# resource "aws_appautoscaling_policy" "gateway_cpu" {
#   name               = "${local.name_prefix}-gateway-cpu"
#   policy_type        = "TargetTrackingScaling"
#   resource_id        = aws_appautoscaling_target.gateway.resource_id
#   scalable_dimension = aws_appautoscaling_target.gateway.scalable_dimension
#   service_namespace  = aws_appautoscaling_target.gateway.service_namespace
#
#   target_tracking_scaling_policy_configuration {
#     target_value = 60
#     predefined_metric_specification {
#       predefined_metric_type = "ECSServiceAverageCPUUtilization"
#     }
#   }
# }
