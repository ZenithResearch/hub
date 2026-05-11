resource "aws_ecs_cluster" "this" {
  name = "${local.name_prefix}-cluster"
  tags = merge(local.tags, { Name = "${local.name_prefix}-cluster" })
}

resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = "${local.name_prefix}.local"
  description = "Private service discovery for agent platform services"
  vpc         = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}.local" })
}

resource "aws_service_discovery_service" "runtime" {
  name = "runtime-grpc"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id
    dns_records {
      type = "A"
      ttl  = 10
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = local.tags
}

resource "aws_service_discovery_service" "tool" {
  name = "tool-sandbox"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id
    dns_records {
      type = "A"
      ttl  = 10
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = local.tags
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name_prefix}-ecs-exec-role"
  assume_role_policy = data.aws_iam_policy_document.task_execution_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "task_execution_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.task_execution_assume.json
  tags               = local.tags
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${local.name_prefix}/gateway-http"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "runtime" {
  name              = "/ecs/${local.name_prefix}/runtime-grpc"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "tool" {
  name              = "/ecs/${local.name_prefix}/tool-sandbox"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_cloudwatch_log_group" "indexer" {
  name              = "/ecs/${local.name_prefix}/kb-indexer"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

locals {
  runtime_grpc_target = "runtime-grpc.${aws_service_discovery_private_dns_namespace.this.name}:50051"
  tool_grpc_target    = "tool-sandbox.${aws_service_discovery_private_dns_namespace.this.name}:50052"
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${local.name_prefix}-gateway-http"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = var.container_image
      essential = true
      command = [
        "uvicorn",
        "services.gateway_http.app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--timeout-keep-alive",
        "5"
      ]
      portMappings = [
        { containerPort = 8080, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "HTTP_PORT", value = "8080" },
        { name = "RUNTIME_GRPC_TARGET", value = local.runtime_grpc_target },
        { name = "CORS_ALLOW_ORIGINS", value = var.cors_allow_origins },
        { name = "MAX_BODY_BYTES", value = tostring(var.max_body_bytes) },
        { name = "GATEWAY_GRPC_TIMEOUT_S", value = "5.0" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.gateway.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_task_definition" "runtime" {
  family                   = "${local.name_prefix}-runtime-grpc"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    merge(
      {
        name      = "app"
        image     = var.container_image
        essential = true
        command   = ["python", "-m", "services.runtime_grpc.main"]
        portMappings = [
          { containerPort = 50051, protocol = "tcp" }
        ]
        environment = [
          { name = "LOG_LEVEL", value = "info" },
          { name = "RUNTIME_GRPC_BIND", value = "0.0.0.0:50051" },
          { name = "TOOL_SANDBOX_GRPC_TARGET", value = local.tool_grpc_target },
          { name = "QDRANT_URL", value = var.qdrant_url },
          { name = "QDRANT_COLLECTION", value = var.qdrant_collection },
          { name = "KB_VECTOR_DIM", value = tostring(var.kb_vector_dim) },
          { name = "TOOL_DIR", value = var.tool_dir },
          { name = "TOOL_DEFAULT_TIMEOUT_MS", value = tostring(var.tool_default_timeout_ms) },
          { name = "TOOL_DEFAULT_MAX_MEMORY_MB", value = tostring(var.tool_default_max_memory_mb) }
        ]
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = aws_cloudwatch_log_group.runtime.name
            awslogs-region        = var.aws_region
            awslogs-stream-prefix = "ecs"
          }
        }
      },
      var.qdrant_api_key_secret_arn != "" ? {
        secrets = [
          { name = "QDRANT_API_KEY", valueFrom = var.qdrant_api_key_secret_arn }
        ]
      } : {}
    )
  ])

  tags = local.tags
}

resource "aws_ecs_task_definition" "tool" {
  family                   = "${local.name_prefix}-tool-sandbox"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = var.container_image
      essential = true
      command   = ["python", "-m", "services.tool_sandbox.main"]
      portMappings = [
        { containerPort = 50052, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "TOOL_SANDBOX_GRPC_BIND", value = "0.0.0.0:50052" },
        { name = "TOOL_DIR", value = var.tool_dir },
        { name = "TOOL_DEFAULT_TIMEOUT_MS", value = tostring(var.tool_default_timeout_ms) },
        { name = "TOOL_DEFAULT_MAX_MEMORY_MB", value = tostring(var.tool_default_max_memory_mb) },
        { name = "ALLOW_TOOLS_WITH_NETWORK", value = "false" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tool.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

# Optional one-shot seeder task definition. Run with `aws ecs run-task` or as a scheduled task.
resource "aws_ecs_task_definition" "kb_indexer" {
  family                   = "${local.name_prefix}-kb-indexer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    merge(
      {
        name      = "app"
        image     = var.container_image
        essential = true
        command   = ["python", "-m", "services.kb_indexer.main"]
        environment = [
          { name = "LOG_LEVEL", value = "info" },
          { name = "QDRANT_URL", value = var.qdrant_url },
          { name = "QDRANT_COLLECTION", value = var.qdrant_collection },
          { name = "KB_VECTOR_DIM", value = tostring(var.kb_vector_dim) },
          { name = "KB_SEED_DIR", value = "/app/kb_seed" }
        ]
        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = aws_cloudwatch_log_group.indexer.name
            awslogs-region        = var.aws_region
            awslogs-stream-prefix = "ecs"
          }
        }
      },
      var.qdrant_api_key_secret_arn != "" ? {
        secrets = [
          { name = "QDRANT_API_KEY", valueFrom = var.qdrant_api_key_secret_arn }
        ]
      } : {}
    )
  ])

  tags = local.tags
}

resource "aws_ecs_service" "gateway" {
  name            = "${local.name_prefix}-gateway-http"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = var.gateway_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.gateway.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gateway.arn
    container_name   = "app"
    container_port   = 8080
  }

  health_check_grace_period_seconds = 30

  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  tags = local.tags

  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "runtime" {
  name            = "${local.name_prefix}-runtime-grpc"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.runtime.arn
  desired_count   = var.runtime_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.runtime.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.runtime.arn
  }

  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  tags = local.tags
}

resource "aws_ecs_service" "tool" {
  name            = "${local.name_prefix}-tool-sandbox"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.tool.arn
  desired_count   = var.tool_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tool_sandbox.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.tool.arn
  }

  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  tags = local.tags
}

