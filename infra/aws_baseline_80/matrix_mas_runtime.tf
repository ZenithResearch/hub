# Matrix Authentication Service (MAS) infrastructure for MSC4108 QR login.
# Provisioning, service startup, and authentication cutover are separate gates.

resource "aws_security_group" "matrix_mas" {
  count = var.enable_matrix_mas ? 1 : 0

  name        = "${local.name_prefix}-matrix-mas-sg"
  description = "Private Matrix Authentication Service ingress"
  vpc_id      = aws_vpc.this.id
  tags        = merge(local.tags, { Name = "${local.name_prefix}-matrix-mas-sg" })
}

resource "aws_security_group_rule" "matrix_mas_web_from_alb" {
  count = var.enable_matrix_mas ? 1 : 0

  type                     = "ingress"
  description              = "MAS web from ALB"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb.id
  security_group_id        = aws_security_group.matrix_mas[0].id
}

resource "aws_security_group_rule" "matrix_mas_health_from_alb" {
  count = var.enable_matrix_mas ? 1 : 0

  type                     = "ingress"
  description              = "MAS health from ALB"
  from_port                = 8081
  to_port                  = 8081
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb.id
  security_group_id        = aws_security_group.matrix_mas[0].id
}

resource "aws_security_group_rule" "matrix_mas_https_control_plane" {
  count = var.enable_matrix_mas ? 1 : 0

  type              = "egress"
  description       = "HTTPS control plane"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.matrix_mas[0].id
}

resource "aws_security_group_rule" "matrix_mas_private_postgres" {
  count = var.enable_matrix_mas ? 1 : 0

  type              = "egress"
  description       = "Private PostgreSQL"
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  cidr_blocks       = [var.vpc_cidr]
  security_group_id = aws_security_group.matrix_mas[0].id
}

resource "aws_security_group_rule" "matrix_mas_dns_udp" {
  count = var.enable_matrix_mas ? 1 : 0

  type              = "egress"
  description       = "VPC DNS UDP"
  from_port         = 53
  to_port           = 53
  protocol          = "udp"
  cidr_blocks       = [var.vpc_cidr]
  security_group_id = aws_security_group.matrix_mas[0].id
}

resource "aws_security_group_rule" "matrix_mas_dns_tcp" {
  count = var.enable_matrix_mas ? 1 : 0

  type              = "egress"
  description       = "VPC DNS TCP"
  from_port         = 53
  to_port           = 53
  protocol          = "tcp"
  cidr_blocks       = [var.vpc_cidr]
  security_group_id = aws_security_group.matrix_mas[0].id
}

resource "aws_security_group_rule" "matrix_mas_from_synapse" {
  count = var.matrix_mas_cutover_complete ? 1 : 0

  type                     = "ingress"
  description              = "Synapse delegated authentication to MAS"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.matrix.id
  security_group_id        = aws_security_group.matrix_mas[0].id
}


resource "aws_security_group" "matrix_mas_postgres" {
  count = var.enable_matrix_mas ? 1 : 0

  name        = "${local.name_prefix}-matrix-mas-db-sg"
  description = "Private MAS PostgreSQL ingress from MAS only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "postgres_from_mas"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.matrix_mas[0].id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-mas-db-sg" })
}

resource "aws_db_instance" "matrix_mas" {
  count = var.enable_matrix_mas ? 1 : 0

  identifier                            = "${local.name_prefix}-matrix-mas"
  engine                                = "postgres"
  engine_version                        = var.matrix_mas_postgres_engine_version
  instance_class                        = var.matrix_mas_postgres_instance_class
  allocated_storage                     = var.matrix_mas_postgres_allocated_storage_gb
  max_allocated_storage                 = var.matrix_mas_postgres_max_allocated_storage_gb
  storage_type                          = "gp3"
  storage_encrypted                     = true
  db_name                               = "mas"
  username                              = "mas"
  manage_master_user_password           = true
  db_subnet_group_name                  = aws_db_subnet_group.matrix_synapse[0].name
  vpc_security_group_ids                = [aws_security_group.matrix_mas_postgres[0].id]
  publicly_accessible                   = false
  multi_az                              = var.matrix_mas_postgres_multi_az
  backup_retention_period               = var.matrix_mas_backup_retention_days
  deletion_protection                   = var.matrix_mas_deletion_protection
  skip_final_snapshot                   = false
  final_snapshot_identifier             = "${local.name_prefix}-matrix-mas-final"
  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-mas-db" })
}

resource "aws_cloudwatch_log_group" "matrix_mas" {
  count = var.enable_matrix_mas ? 1 : 0

  name              = "/ecs/${local.name_prefix}/matrix-mas"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_lb_target_group" "matrix_mas" {
  count = var.enable_matrix_mas ? 1 : 0

  name        = "${local.name_prefix}-matrix-mas"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  health_check {
    enabled = true
    path    = "/health"
    port    = "8081"
    matcher = "200"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-mas-tg" })
}

resource "aws_iam_role" "matrix_mas_execution" {
  count = var.enable_matrix_mas ? 1 : 0

  name               = "${local.name_prefix}-matrix-mas-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "matrix_mas_execution" {
  count = var.enable_matrix_mas ? 1 : 0

  role       = aws_iam_role.matrix_mas_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "matrix_mas_execution_secrets" {
  count = var.enable_matrix_mas ? 1 : 0

  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_db_instance.matrix_mas[0].master_user_secret[0].secret_arn,
      aws_secretsmanager_secret.matrix_mas_synapse_shared_secret.arn,
      aws_secretsmanager_secret.matrix_mas_encryption_secret.arn,
      aws_secretsmanager_secret.matrix_mas_signing_key.arn,
    ]
  }
}

resource "aws_iam_role_policy" "matrix_mas_execution_secrets" {
  count = var.enable_matrix_mas ? 1 : 0

  name   = "${local.name_prefix}-matrix-mas-secrets"
  role   = aws_iam_role.matrix_mas_execution[0].id
  policy = data.aws_iam_policy_document.matrix_mas_execution_secrets[0].json
}

resource "aws_iam_role" "matrix_mas_task" {
  count = var.enable_matrix_mas ? 1 : 0

  name               = "${local.name_prefix}-matrix-mas-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.tags
}

resource "aws_ecs_task_definition" "matrix_mas" {
  count = var.enable_matrix_mas ? 1 : 0

  family                   = "${local.name_prefix}-matrix-mas"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.matrix_mas_task_cpu)
  memory                   = tostring(var.matrix_mas_task_memory)
  execution_role_arn       = aws_iam_role.matrix_mas_execution[0].arn
  task_role_arn            = aws_iam_role.matrix_mas_task[0].arn

  container_definitions = jsonencode([
    {
      name      = "mas"
      image     = var.matrix_mas_image
      essential = true
      portMappings = [
        { containerPort = 8080, protocol = "tcp" },
        { containerPort = 8081, protocol = "tcp" },
      ]
      environment = [
        { name = "MATRIX_MAS_DB_HOST", value = aws_db_instance.matrix_mas[0].address },
        { name = "MATRIX_MAS_PUBLIC_BASE", value = "https://${var.public_matrix_auth_domain_name}/" },
        { name = "MATRIX_MAS_SYNAPSE_ENDPOINT", value = "https://${var.public_matrix_domain_name}" },
        { name = "MATRIX_MAS_VPC_CIDR", value = var.vpc_cidr },
        { name = "MATRIX_MAS_CUTOVER_COMPLETE", value = tostring(var.matrix_mas_cutover_complete) },
      ]
      secrets = [
        {
          name      = "MATRIX_MAS_DB_PASSWORD"
          valueFrom = "${aws_db_instance.matrix_mas[0].master_user_secret[0].secret_arn}:password::"
        },
        {
          name      = "MATRIX_MAS_SYNAPSE_SHARED_SECRET"
          valueFrom = aws_secretsmanager_secret.matrix_mas_synapse_shared_secret.arn
        },
        {
          name      = "MATRIX_MAS_ENCRYPTION_SECRET"
          valueFrom = aws_secretsmanager_secret.matrix_mas_encryption_secret.arn
        },
        {
          name      = "MATRIX_MAS_SIGNING_KEY"
          valueFrom = aws_secretsmanager_secret.matrix_mas_signing_key.arn
        },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.matrix_mas[0].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  lifecycle {
    precondition {
      condition     = var.enable_matrix_synapse
      error_message = "enable_matrix_mas requires enable_matrix_synapse."
    }
    precondition {
      condition     = !var.start_matrix_mas_service || var.enable_matrix_mas
      error_message = "start_matrix_mas_service requires enable_matrix_mas."
    }
    precondition {
      condition     = !var.start_matrix_mas_service || var.enable_matrix_mas_public_edge
      error_message = "start_matrix_mas_service requires enable_matrix_mas_public_edge after the auth certificate is issued."
    }
    precondition {
      condition     = var.matrix_mas_desired_count == 1
      error_message = "Initial MAS rollout requires matrix_mas_desired_count == 1."
    }
    precondition {
      condition = !var.start_matrix_mas_service || can(regex(
        "^${data.aws_caller_identity.current.account_id}\\.dkr\\.ecr\\.${data.aws_region.current.name}\\.amazonaws\\.com/.+@sha256:[0-9a-f]{64}$",
        var.matrix_mas_image,
      ))
      error_message = "Starting production MAS requires a reviewed digest-pinned image mirrored into this AWS account."
    }
  }

  tags = local.tags
}

resource "aws_ecs_service" "matrix_mas" {
  count = var.enable_matrix_mas && var.enable_matrix_mas_public_edge ? 1 : 0

  name            = "${local.name_prefix}-matrix-mas"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.matrix_mas[0].arn
  desired_count   = var.enable_matrix_mas && var.start_ecs_services && var.start_matrix_mas_service ? var.matrix_mas_desired_count : 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.matrix_mas[0].id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.matrix_mas[0].arn
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.matrix_mas[0].arn
    container_name   = "mas"
    container_port   = 8080
  }

  health_check_grace_period_seconds  = 120
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    precondition {
      condition     = !var.start_matrix_mas_service || var.enable_matrix_backup
      error_message = "Starting MAS requires enable_matrix_backup."
    }
    precondition {
      condition     = !var.start_matrix_mas_service || var.matrix_alarm_email != "" || length(var.matrix_alarm_actions) > 0
      error_message = "Starting MAS requires a Matrix incident destination."
    }
  }

  depends_on = [aws_lb_listener_rule.matrix_mas_auth_host]
  tags       = local.tags
}

resource "aws_security_group_rule" "matrix_mas_to_synapse_efs" {
  count = var.enable_matrix_mas_migration_task ? 1 : 0

  type                     = "egress"
  description              = "Reviewed syn2mas migration task to Synapse EFS"
  from_port                = 2049
  to_port                  = 2049
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.matrix_synapse_efs[0].id
  security_group_id        = aws_security_group.matrix_mas[0].id
}

resource "aws_ecs_task_definition" "matrix_mas_migration" {
  count = var.enable_matrix_mas_migration_task ? 1 : 0

  family                   = "${local.name_prefix}-matrix-mas-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.matrix_mas_task_cpu)
  memory                   = tostring(var.matrix_mas_task_memory)
  execution_role_arn       = aws_iam_role.matrix_mas_execution[0].arn
  task_role_arn            = aws_iam_role.matrix_mas_task[0].arn

  volume {
    name = "synapse-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.matrix_synapse[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.matrix_synapse[0].id
        iam             = "DISABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "syn2mas"
      image     = var.matrix_mas_image
      essential = true
      # Safe default command: syn2mas check. Operators override only with the
      # reviewed dry-run or stopped-Synapse migration command from the runbook.
      command = ["syn2mas", "check", "--synapse-config", "/synapse-data/homeserver.yaml"]
      environment = [
        { name = "MATRIX_MAS_DB_HOST", value = aws_db_instance.matrix_mas[0].address },
        { name = "MATRIX_MAS_PUBLIC_BASE", value = "https://${var.public_matrix_auth_domain_name}/" },
        { name = "MATRIX_MAS_SYNAPSE_ENDPOINT", value = "https://${var.public_matrix_domain_name}" },
        { name = "MATRIX_MAS_VPC_CIDR", value = var.vpc_cidr },
        { name = "MATRIX_MAS_CUTOVER_COMPLETE", value = "false" },
      ]
      secrets = [
        {
          name      = "MATRIX_MAS_DB_PASSWORD"
          valueFrom = "${aws_db_instance.matrix_mas[0].master_user_secret[0].secret_arn}:password::"
        },
        {
          name      = "MATRIX_MAS_SYNAPSE_SHARED_SECRET"
          valueFrom = aws_secretsmanager_secret.matrix_mas_synapse_shared_secret.arn
        },
        {
          name      = "MATRIX_MAS_ENCRYPTION_SECRET"
          valueFrom = aws_secretsmanager_secret.matrix_mas_encryption_secret.arn
        },
        {
          name      = "MATRIX_MAS_SIGNING_KEY"
          valueFrom = aws_secretsmanager_secret.matrix_mas_signing_key.arn
        },
      ]
      mountPoints = [{
        sourceVolume  = "synapse-data"
        containerPath = "/synapse-data"
        readOnly      = true
      }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.matrix_mas[0].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "syn2mas"
        }
      }
    }
  ])

  lifecycle {
    precondition {
      condition     = var.enable_matrix_mas && var.enable_matrix_synapse
      error_message = "enable_matrix_mas_migration_task requires MAS and Synapse infrastructure."
    }
    precondition {
      condition = can(regex(
        "^${data.aws_caller_identity.current.account_id}\\.dkr\\.ecr\\.${data.aws_region.current.name}\\.amazonaws\\.com/.+@sha256:[0-9a-f]{64}$",
        var.matrix_mas_image,
      ))
      error_message = "The migration task requires the reviewed digest-pinned MAS wrapper image in this AWS account."
    }
  }

  depends_on = [aws_efs_mount_target.matrix_synapse]
  tags       = local.tags
}
