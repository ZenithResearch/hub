resource "aws_ecs_cluster" "this" {
  name = "${local.name_prefix}-cluster"
  tags = merge(local.tags, { Name = "${local.name_prefix}-cluster" })
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${local.name_prefix}-gateway-http"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.gateway_task.arn

  volume {
    name = "gateway-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.gateway.id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.gateway.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.gateway.repository_url}:${var.image_tag}"
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
        { name = "RUNTIME_GRPC_TARGET", value = local.runtime_target },
        { name = "CLIENTS_DB_BACKEND", value = var.enable_clients_postgres ? "postgres" : "sqlite" },
        { name = "CLIENTS_DB_PATH", value = var.gateway_clients_db_path },
        { name = "CLIENTS_PG_HOST", value = var.enable_clients_postgres ? aws_db_instance.clients[0].address : "" },
        { name = "CLIENTS_PG_PORT", value = var.enable_clients_postgres ? tostring(aws_db_instance.clients[0].port) : "5432" },
        { name = "CLIENTS_PG_DATABASE", value = var.clients_postgres_database_name },
        { name = "CLIENTS_PG_USER", value = var.clients_postgres_username },
        { name = "REVIEWS_DATA_DIR", value = var.gateway_reviews_data_dir },
        { name = "CORS_ALLOW_ORIGINS", value = var.cors_allow_origins },
        { name = "MAX_BODY_BYTES", value = tostring(var.max_body_bytes) },
        { name = "GATEWAY_GRPC_TIMEOUT_S", value = "5.0" }
      ]
      secrets = concat(
        var.enable_clients_postgres ? [
          {
            name      = "CLIENTS_PG_PASSWORD"
            valueFrom = "${aws_db_instance.clients[0].master_user_secret[0].secret_arn}:password::"
          }
        ] : [],
        [
          {
            name      = "REVIEW_ACCESS_ADMIN_TOKEN"
            valueFrom = aws_secretsmanager_secret.review_access_admin_token.arn
          }
        ]
      )
      mountPoints = [
        {
          sourceVolume  = "gateway-data"
          containerPath = "/data"
          readOnly      = false
        }
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
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.runtime_task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.runtime.repository_url}:${var.image_tag}"
      essential = true
      command   = ["python", "-m", "services.runtime_grpc.main"]
      portMappings = [
        { containerPort = 50051, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "RUNTIME_GRPC_BIND", value = "0.0.0.0:50051" },
        { name = "TOOL_SANDBOX_GRPC_TARGET", value = local.sandbox_target },
        { name = "QDRANT_URL", value = var.qdrant_url },
        { name = "QDRANT_COLLECTION", value = var.qdrant_collection },
        { name = "KB_VECTOR_DIM", value = tostring(var.kb_vector_dim) },
        { name = "TOOL_DIR", value = var.tool_dir },
        { name = "TOOL_DEFAULT_TIMEOUT_MS", value = tostring(var.tool_default_timeout_ms) },
        { name = "TOOL_DEFAULT_MAX_MEMORY_MB", value = tostring(var.tool_default_max_memory_mb) }
      ]
      secrets = [
        { name = "QDRANT_API_KEY", valueFrom = aws_secretsmanager_secret.qdrant_api_key.arn }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.runtime.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_task_definition" "sandbox" {
  family                   = "${local.name_prefix}-tool-sandbox"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.sandbox_task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.sandbox.repository_url}:${var.image_tag}"
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
          awslogs-group         = aws_cloudwatch_log_group.sandbox.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "gateway" {
  name            = "${local.name_prefix}-gateway-http"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = var.start_ecs_services ? var.gateway_desired_count : 0
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

  health_check_grace_period_seconds  = 60
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200

  depends_on = [
    aws_lb_listener.http,
    aws_efs_mount_target.gateway,
  ]
  tags = local.tags
}

resource "aws_ecs_service" "runtime" {
  name            = "${local.name_prefix}-runtime-grpc"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.runtime.arn
  desired_count   = var.start_ecs_services ? var.runtime_desired_count : 0
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

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200

  tags = local.tags
}

resource "aws_ecs_service" "sandbox" {
  name            = "${local.name_prefix}-tool-sandbox"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.sandbox.arn
  desired_count   = var.start_ecs_services ? var.sandbox_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.sandbox.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.sandbox.arn
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200

  tags = local.tags
}

# Queue — desired_count MUST remain 1. SQLite WAL is safe for concurrent readers
# but only a single writer. Multiple replicas would corrupt the database.
resource "aws_ecs_task_definition" "queue" {
  family                   = "${local.name_prefix}-queue"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.queue_task.arn

  # EFS volume — maps the access point to /data inside the container.
  volume {
    name = "queue-data"

    efs_volume_configuration {
      file_system_id          = aws_efs_file_system.queue.id
      root_directory          = "/"
      transit_encryption      = "ENABLED"
      transit_encryption_port = 2999

      authorization_config {
        access_point_id = aws_efs_access_point.queue.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.queue.repository_url}:${var.image_tag}"
      essential = true
      command   = ["python", "-m", "inbox.main"]
      portMappings = [
        { containerPort = 50053, protocol = "tcp" },
        { containerPort = 8081, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "QUEUE_GRPC_BIND", value = "0.0.0.0:50053" },
        { name = "QUEUE_HTTP_PORT", value = "8081" },
        { name = "QUEUE_DB_PATH", value = "/data/queue.db" },
        { name = "QUEUE_REAPER_INTERVAL_S", value = "30" },
        { name = "QUEUE_DEFAULT_MAX_RETRIES", value = "3" },
        { name = "QUEUE_DEFAULT_CLAIM_TIMEOUT_S", value = "300" }
      ]
      mountPoints = [
        {
          sourceVolume  = "queue-data"
          containerPath = "/data"
          readOnly      = false
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.queue.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "queue" {
  name            = "${local.name_prefix}-queue"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.queue.arn
  desired_count   = var.start_ecs_services ? var.queue_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.queue.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.queue.arn
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  tags = local.tags
}

