resource "aws_ecs_cluster" "this" {
  name = "${local.name_prefix}-cluster"
  tags = merge(local.tags, { Name = "${local.name_prefix}-cluster" })
}

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${local.name_prefix}-gateway-http"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.gateway_task_cpu)
  memory                   = tostring(var.gateway_task_memory)
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

  volume {
    name = "frank-execution-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.frank.id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.frank_execution.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.gateway.repository_url}:${local.gateway_image_tag}"
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
        { name = "AGENT_ADMIN_GRPC_TARGET", value = local.agent_admin_target },
        { name = "CLIENTS_DB_BACKEND", value = var.enable_clients_postgres ? "postgres" : "sqlite" },
        { name = "CLIENTS_DB_PATH", value = var.gateway_clients_db_path },
        { name = "CLIENTS_PG_HOST", value = var.enable_clients_postgres ? aws_db_instance.clients[0].address : "" },
        { name = "CLIENTS_PG_PORT", value = var.enable_clients_postgres ? tostring(aws_db_instance.clients[0].port) : "5432" },
        { name = "CLIENTS_PG_DATABASE", value = var.clients_postgres_database_name },
        { name = "CLIENTS_PG_USER", value = var.clients_postgres_username },
        { name = "REVIEWS_DATA_DIR", value = var.gateway_reviews_data_dir },
        { name = "MODEL_PROFILES_PATH", value = var.gateway_model_profiles_path },
        { name = "MODEL_PROFILE_OVERRIDES_PATH", value = var.gateway_model_profile_overrides_path },
        { name = "MODEL_PROFILE_AUDIT_PATH", value = var.gateway_model_profile_audit_path },
        { name = "QUEUE_HTTP_URL", value = "http://${local.queue_http_target}" },
        { name = "CASES_HTTP_URL", value = "http://${local.cases_http_target}" },
        { name = "EVENTBUS_URL", value = "http://${local.eventbus_target}" },
        { name = "HUBFS_ALLOWED_ROOTS", value = "/data:/app/base/ops/processes" },
        { name = "MATRIX_HOMESERVER_URL", value = var.enable_matrix_synapse ? "https://${var.public_matrix_domain_name}" : "" },
        { name = "MATRIX_GATEWAY_BOT_USER_ID", value = var.enable_matrix_synapse ? "@gateway-bot:${var.public_matrix_domain_name}" : "" },
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
        ],
        var.enable_hermes_cloud_agent ? [
          {
            name      = "AGENT_ADMIN_BEARER_TOKEN"
            valueFrom = aws_secretsmanager_secret.agent_admin_bearer_token.arn
          }
        ] : []
      )
      mountPoints = [
        {
          sourceVolume  = "gateway-data"
          containerPath = "/data"
          readOnly      = false
        },
        {
          sourceVolume  = "frank-execution-data"
          containerPath = "/data/frank_execution"
          readOnly      = true
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

  lifecycle {
    precondition {
      condition = (
        !var.enable_hermes_cloud_agent ||
        var.agent_admin_bearer_token != "" ||
        var.agent_admin_bearer_token_secret_ready
      )
      error_message = "Populate the managed Agent Admin bearer secret out-of-band and attest readiness before enabling the Hermes cloud agent."
    }
  }

  depends_on = [aws_secretsmanager_secret_version.agent_admin_bearer_token]
}

resource "aws_ecs_task_definition" "runtime" {
  family                   = "${local.name_prefix}-runtime-grpc"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.runtime_task_cpu)
  memory                   = tostring(var.runtime_task_memory)
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
  cpu                      = tostring(var.sandbox_task_cpu)
  memory                   = tostring(var.sandbox_task_memory)
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
  cpu                      = tostring(var.queue_task_cpu)
  memory                   = tostring(var.queue_task_memory)
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

# Cases — desired_count MUST remain 1. SQLite WAL is safe for concurrent readers
# but only a single writer. Multiple replicas would corrupt the database.
resource "aws_ecs_task_definition" "cases" {
  family                   = "${local.name_prefix}-cases"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.cases_task_cpu)
  memory                   = tostring(var.cases_task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.cases_task.arn

  volume {
    name = "cases-data"

    efs_volume_configuration {
      file_system_id          = aws_efs_file_system.cases.id
      root_directory          = "/"
      transit_encryption      = "ENABLED"
      transit_encryption_port = 2999

      authorization_config {
        access_point_id = aws_efs_access_point.cases.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.gateway.repository_url}:${local.cases_image_tag}"
      essential = true
      command   = ["uvicorn", "services.cases.main:app", "--host", "0.0.0.0", "--port", "8083", "--timeout-keep-alive", "5"]
      portMappings = [
        { containerPort = 8083, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "CASES_HTTP_PORT", value = "8083" },
        { name = "CASES_DB_PATH", value = "/data/cases.db" }
      ]
      mountPoints = [
        {
          sourceVolume  = "cases-data"
          containerPath = "/data"
          readOnly      = false
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.cases.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "cases" {
  name            = "${local.name_prefix}-cases"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.cases.arn
  desired_count   = var.start_ecs_services ? var.cases_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.cases.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.cases.arn
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  depends_on = [
    aws_efs_mount_target.cases,
  ]

  tags = local.tags
}



resource "aws_ecs_task_definition" "eventbus" {
  family                   = "${local.name_prefix}-eventbus"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.eventbus_task_cpu)
  memory                   = tostring(var.eventbus_task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.gateway_task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.gateway.repository_url}:${local.eventbus_image_tag}"
      essential = true
      command   = ["python", "-m", "services.eventbus.main"]
      portMappings = [
        { containerPort = 8082, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "PYTHONPATH", value = "/app" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.eventbus.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "eventbus" {
  name            = "${local.name_prefix}-eventbus"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.eventbus.arn
  desired_count   = var.start_ecs_services ? var.eventbus_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.eventbus.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.eventbus.arn
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  tags = local.tags
}

resource "aws_ecs_task_definition" "frank" {
  family                   = "${local.name_prefix}-frank"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.frank_task_cpu)
  memory                   = tostring(var.frank_task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.frank_task.arn

  volume {
    name = "frank-data"

    efs_volume_configuration {
      file_system_id          = aws_efs_file_system.frank.id
      root_directory          = "/"
      transit_encryption      = "ENABLED"
      transit_encryption_port = 2999

      authorization_config {
        access_point_id = aws_efs_access_point.frank.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.gateway.repository_url}:${local.frank_image_tag}"
      essential = true
      command = [
        "python",
        "-c",
        <<-PY
import asyncio
import httpx
from services.frank.main import handle_enqueued, main

async def boot() -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(50):
            await handle_enqueued(client)
    await main()

asyncio.run(boot())
PY
      ]
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "PYTHONPATH", value = "/app" },
        { name = "QUEUE_HTTP_URL", value = "http://${local.queue_http_target}" },
        { name = "EVENTBUS_URL", value = "http://${local.eventbus_target}" },
        { name = "CASES_HTTP_URL", value = "http://${local.cases_http_target}" },
        { name = "GATEWAY_HTTP_URL", value = var.public_hub_domain_name != "" ? "https://${var.public_hub_domain_name}" : "http://${aws_lb.gateway.dns_name}" },
        { name = "STT_HTTP_URL", value = "http://${local.stt_http_target}" },
        { name = "QUEUE_NAME", value = "workspace" },
        { name = "TERMINAL_CWD", value = "/app" },
        { name = "PROCESS_HUBFS_ROOT", value = "/app/base/ops/processes" },
        { name = "HERMES_HOME", value = "/data/hermes" },
        { name = "HERMES_PROFILE_ROOT", value = "/data/hermes/profiles" },
        { name = "FRANK_EXECUTION_ROOT", value = "/data/frank_execution" },
        { name = "FRANK_RUNTIME", value = "native_case_pipeline" },
        { name = "STT_PROVIDER", value = var.stt_provider },
        { name = "STT_MODEL", value = var.stt_model },
        { name = "STT_FALLBACK_PROVIDER", value = var.stt_fallback_provider },
        { name = "STT_AUDIO_PREPROCESSOR", value = var.stt_audio_preprocessor },
        { name = "FRANK_MODEL", value = var.frank_model },
        { name = "OPENAI_BASE_URL", value = var.frank_openai_base_url },
        { name = "OPENAI_API_KEY", value = "none" }
      ]
      secrets = var.elevenlabs_api_key_secret_arn != "" ? [
        { name = "ELEVENLABS_API_KEY", valueFrom = var.elevenlabs_api_key_secret_arn }
      ] : []
      mountPoints = [
        {
          sourceVolume  = "frank-data"
          containerPath = "/data"
          readOnly      = false
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.frank.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "frank" {
  name            = "${local.name_prefix}-frank"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.frank.arn
  desired_count   = var.start_ecs_services ? var.frank_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.frank.id]
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  depends_on = [
    aws_ecs_service.eventbus,
    aws_ecs_service.queue,
    aws_ecs_service.cases,
    aws_efs_mount_target.frank,
  ]

  tags = local.tags
}


resource "aws_ecs_task_definition" "stt_http" {
  family                   = "${local.name_prefix}-stt-http"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.stt_http_task_cpu)
  memory                   = tostring(var.stt_http_task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.stt_http_task.arn

  volume {
    name = "frank-data"

    efs_volume_configuration {
      file_system_id          = aws_efs_file_system.frank.id
      root_directory          = "/"
      transit_encryption      = "ENABLED"
      transit_encryption_port = 2999

      authorization_config {
        access_point_id = aws_efs_access_point.frank.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.gateway.repository_url}:${local.stt_image_tag}"
      essential = true
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "PYTHONPATH", value = "/app" },
        { name = "STT_ALLOWED_AUDIO_ROOTS", value = "/data/frank_execution" },
        { name = "STT_WHISPER_MODEL", value = "tiny" },
        { name = "STT_ALLOWED_WHISPER_MODELS", value = "tiny,base,small" }
      ]
      portMappings = [
        { containerPort = 8765, protocol = "tcp" }
      ]
      mountPoints = [
        {
          sourceVolume  = "frank-data"
          containerPath = "/data"
          readOnly      = true
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.stt_http.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "stt_http" {
  name            = "${local.name_prefix}-stt-http"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.stt_http.arn
  desired_count   = var.start_ecs_services ? var.stt_http_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.stt_http.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.stt_http.arn
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  depends_on = [
    aws_efs_mount_target.frank,
  ]

  tags = local.tags
}


resource "aws_ecs_task_definition" "llama_server" {
  family                   = "${local.name_prefix}-llama-server"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.llama_server_task_cpu)
  memory                   = tostring(var.llama_server_task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.llama_server_task.arn

  volume {
    name = "frank-data"

    efs_volume_configuration {
      file_system_id          = aws_efs_file_system.frank.id
      root_directory          = "/"
      transit_encryption      = "ENABLED"
      transit_encryption_port = 2999

      authorization_config {
        access_point_id = aws_efs_access_point.frank.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = var.llama_server_image
      essential = true
      command = [
        "-m",
        "/models/llama/${var.llama_server_model_name}",
        "--host",
        "0.0.0.0",
        "--port",
        "3690",
        "-c",
        "4096",
        "-t",
        "4",
        "-ngl",
        "0",
        "--reasoning",
        "off"
      ]
      portMappings = [
        { containerPort = 3690, hostPort = 3690, protocol = "tcp" }
      ]
      environment = []
      mountPoints = [
        {
          sourceVolume  = "frank-data"
          containerPath = "/models"
          readOnly      = true
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.llama_server.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "llama_server" {
  name            = "${local.name_prefix}-llama-server"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.llama_server.arn
  desired_count   = var.start_ecs_services ? var.llama_server_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.llama_server.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.llama_server.arn
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  depends_on = [
    aws_efs_mount_target.frank,
  ]

  tags = local.tags
}


resource "aws_ecs_task_definition" "llama_model_preload" {
  family                   = "${local.name_prefix}-llama-model-preload"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.llama_server_task.arn

  volume {
    name = "frank-data"

    efs_volume_configuration {
      file_system_id          = aws_efs_file_system.frank.id
      root_directory          = "/"
      transit_encryption      = "ENABLED"
      transit_encryption_port = 2999

      authorization_config {
        access_point_id = aws_efs_access_point.frank.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name       = "preload"
      image      = var.llama_model_preload_image
      essential  = true
      entryPoint = ["sh", "-lc"]
      command = [
        <<-SH
set -euo pipefail
: "$${MODEL_BUCKET:?MODEL_BUCKET is required}"
: "$${MODEL_KEY:?MODEL_KEY is required}"
: "$${MODEL_NAME:?MODEL_NAME is required}"
TARGET_DIR="/models/llama"
TARGET="$${TARGET_DIR}/$${MODEL_NAME}"
TMP="$${TARGET}.tmp"
mkdir -p "$${TARGET_DIR}"
echo "Downloading s3://$${MODEL_BUCKET}/$${MODEL_KEY} to $${TARGET}"
aws s3 cp "s3://$${MODEL_BUCKET}/$${MODEL_KEY}" "$${TMP}" --no-progress
if [ -n "$${EXPECTED_SHA256:-}" ]; then
  ACTUAL_SHA256="$(sha256sum "$${TMP}" | awk '{print $1}')"
  if [ "$${ACTUAL_SHA256}" != "$${EXPECTED_SHA256}" ]; then
    echo "SHA256 mismatch for $${MODEL_NAME}: expected $${EXPECTED_SHA256}, got $${ACTUAL_SHA256}" >&2
    rm -f "$${TMP}"
    exit 42
  fi
  echo "SHA256 verified: $${ACTUAL_SHA256}"
else
  sha256sum "$${TMP}"
fi
mv "$${TMP}" "$${TARGET}"
ls -lh "$${TARGET}"
        SH
      ]
      environment = [
        { name = "MODEL_BUCKET", value = local.llama_server_model_bucket_name },
        { name = "MODEL_KEY", value = var.llama_server_model_s3_key },
        { name = "MODEL_NAME", value = var.llama_server_model_name },
        { name = "EXPECTED_SHA256", value = var.llama_server_model_expected_sha256 }
      ]
      mountPoints = [
        {
          sourceVolume  = "frank-data"
          containerPath = "/models"
          readOnly      = false
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.llama_server.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "preload"
        }
      }
    }
  ])

  tags = merge(local.tags, { Purpose = "llama-model-preload" })
}

resource "aws_ecs_task_definition" "agent_admin" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  family                   = "${local.name_prefix}-agent-admin"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.agent_admin_task_cpu)
  memory                   = tostring(var.agent_admin_task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.agent_admin_task[0].arn

  volume {
    name = "agent-admin-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.agent_admin[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.agent_admin[0].id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name                   = "app"
      image                  = "${aws_ecr_repository.gateway.repository_url}:${local.agent_admin_image_tag}"
      essential              = true
      command                = ["python", "-m", "services.agent_admin.main"]
      readonlyRootFilesystem = true
      user                   = "1000:1000"
      portMappings = [
        { containerPort = 50054, protocol = "tcp" }
      ]
      environment = [
        { name = "LOG_LEVEL", value = "info" },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "AGENT_ADMIN_GRPC_BIND", value = "0.0.0.0:50054" },
        { name = "AGENT_ADMIN_DB_PATH", value = var.agent_admin_db_path },
        { name = "AGENT_ADMIN_PROFILE_ID", value = var.hermes_cloud_agent_profile_id },
        { name = "AGENT_ADMIN_INSTANCE_ID", value = aws_instance.hermes_cloud_agent[0].id },
        { name = "AGENT_ADMIN_SSM_DOCUMENT_NAME", value = aws_ssm_document.hermes_cloud_agent_control[0].name },
        { name = "AGENT_ADMIN_ALLOWED_MATRIX_SECRET_ARNS", value = var.hermes_cloud_agent_matrix_secret_arn }
      ]
      mountPoints = [
        {
          sourceVolume  = "agent-admin-data"
          containerPath = "/data"
          readOnly      = false
        }
      ]
      linuxParameters = {
        capabilities = { drop = ["ALL"] }
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.agent_admin[0].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "agent_admin" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  name            = "${local.name_prefix}-agent-admin"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.agent_admin[0].arn
  desired_count   = var.start_ecs_services ? var.agent_admin_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = false

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.agent_admin[0].id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.agent_admin[0].arn
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  depends_on = [aws_efs_mount_target.agent_admin]
  tags       = local.tags
}
