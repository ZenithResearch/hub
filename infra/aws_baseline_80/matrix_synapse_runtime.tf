# Concrete production Synapse target for ISS-P14-007.
# Disabled by default: enabling it requires populated Matrix secret handles and an
# operator-reviewed plan. Runtime state is private RDS + encrypted EFS; only the
# existing ALB reaches the client listener.

resource "aws_db_subnet_group" "matrix_synapse" {
  count = var.enable_matrix_synapse ? 1 : 0

  name       = "${local.name_prefix}-matrix-db-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = merge(local.tags, { Name = "${local.name_prefix}-matrix-db-subnets" })
}

resource "aws_security_group" "matrix_synapse_postgres" {
  count = var.enable_matrix_synapse ? 1 : 0

  name        = "${local.name_prefix}-matrix-db-sg"
  description = "Private Synapse Postgres ingress from the Synapse task only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "postgres_from_synapse"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.matrix.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-db-sg" })
}

resource "aws_db_instance" "matrix_synapse" {
  count = var.enable_matrix_synapse ? 1 : 0

  identifier = "${local.name_prefix}-matrix-synapse"

  engine         = "postgres"
  engine_version = var.matrix_synapse_postgres_engine_version
  instance_class = var.matrix_synapse_postgres_instance_class

  allocated_storage     = var.matrix_synapse_postgres_allocated_storage_gb
  max_allocated_storage = var.matrix_synapse_postgres_max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "synapse"
  username = "synapse"

  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.matrix_synapse[0].name
  vpc_security_group_ids = [aws_security_group.matrix_synapse_postgres[0].id]
  publicly_accessible    = false
  multi_az               = var.matrix_synapse_postgres_multi_az

  backup_retention_period   = var.matrix_synapse_backup_retention_days
  deletion_protection       = var.matrix_synapse_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name_prefix}-matrix-synapse-final"

  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-synapse-db" })
}

resource "aws_efs_file_system" "matrix_synapse" {
  count = var.enable_matrix_synapse ? 1 : 0

  creation_token   = "${local.name_prefix}-matrix-synapse-data"
  encrypted        = true
  throughput_mode  = "bursting"
  performance_mode = "generalPurpose"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-synapse-efs" })
}

resource "aws_security_group" "matrix_synapse_efs" {
  count = var.enable_matrix_synapse ? 1 : 0

  name        = "${local.name_prefix}-matrix-efs-sg"
  description = "Synapse media/config EFS: NFS ingress from Synapse tasks only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "nfs_from_synapse"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.matrix.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-efs-sg" })
}

resource "aws_efs_mount_target" "matrix_synapse" {
  count = var.enable_matrix_synapse ? length(aws_subnet.private) : 0

  file_system_id  = aws_efs_file_system.matrix_synapse[0].id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.matrix_synapse_efs[0].id]
}

resource "aws_efs_access_point" "matrix_synapse" {
  count = var.enable_matrix_synapse ? 1 : 0

  file_system_id = aws_efs_file_system.matrix_synapse[0].id

  posix_user {
    uid = 991
    gid = 991
  }

  root_directory {
    path = "/data"
    creation_info {
      owner_uid   = 991
      owner_gid   = 991
      permissions = "750"
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-synapse-ap" })
}

resource "aws_cloudwatch_log_group" "matrix_synapse" {
  count = var.enable_matrix_synapse ? 1 : 0

  name              = "/ecs/${local.name_prefix}/matrix-synapse"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_ecs_task_definition" "matrix_synapse" {
  count = var.enable_matrix_synapse ? 1 : 0

  family                   = "${local.name_prefix}-matrix-synapse"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.matrix_synapse_task_cpu)
  memory                   = tostring(var.matrix_synapse_task_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.matrix_synapse_task[0].arn

  volume {
    name = "synapse-data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.matrix_synapse[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.matrix_synapse[0].id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name       = "synapse"
      image      = var.matrix_synapse_image
      user       = "991:991"
      essential  = true
      entryPoint = ["/bin/sh", "-ec"]
      command = [<<-SCRIPT
        umask 077
        python - <<'PY'
        import os
        import hashlib
        import urllib.request
        from pathlib import Path
        import yaml

        server_name = os.environ["SYNAPSE_SERVER_NAME"]
        data_dir = Path("/data")
        ca_path = data_dir / "aws-rds-global-bundle.pem"
        ca_bytes = urllib.request.urlopen(os.environ["SYNAPSE_RDS_CA_BUNDLE_URL"], timeout=30).read()
        ca_digest = hashlib.sha256(ca_bytes).hexdigest()
        if ca_digest != os.environ["SYNAPSE_RDS_CA_BUNDLE_SHA256"]:
            raise SystemExit("RDS CA bundle checksum mismatch")
        ca_path.write_bytes(ca_bytes)
        signing_key_path = data_dir / f"{server_name}.signing.key"
        signing_key_path.write_text(os.environ["SYNAPSE_SIGNING_KEY"].rstrip("\n") + "\n")

        homeserver = {
            "server_name": server_name,
            "public_baseurl": f"https://{server_name}/",
            "pid_file": "/data/homeserver.pid",
            "listeners": [{
                "port": 8008,
                "tls": False,
                "type": "http",
                "x_forwarded": True,
                "bind_addresses": ["0.0.0.0"],
                "resources": [{"names": ["client", "federation"], "compress": False}],
            }],
            "database": {
                "name": "psycopg2",
                "args": {
                    "user": "synapse",
                    "password": os.environ["SYNAPSE_DB_PASSWORD"],
                    "database": "synapse",
                    "host": os.environ["SYNAPSE_DB_HOST"],
                    "port": 5432,
                    "sslmode": "verify-full",
                    "sslrootcert": "/data/aws-rds-global-bundle.pem",
                    "cp_min": 1,
                    "cp_max": 5,
                },
            },
            "log_config": "/data/log.config",
            "media_store_path": "/data/media_store",
            "signing_key_path": str(signing_key_path),
            "macaroon_secret_key": os.environ["SYNAPSE_MACAROON_SECRET_KEY"],
            "registration_shared_secret": os.environ["SYNAPSE_REGISTRATION_SHARED_SECRET"],
            "form_secret": os.environ["SYNAPSE_FORM_SECRET"],
            "enable_registration": False,
            "report_stats": False,
            "suppress_key_server_warning": True,
            "trusted_key_servers": [{"server_name": "matrix.org"}],
        }
        (data_dir / "homeserver.yaml").write_text(yaml.safe_dump(homeserver, sort_keys=False))

        log_config = {
            "version": 1,
            "formatters": {"precise": {"format": "%(asctime)s - %(name)s - %(lineno)d - %(levelname)s - %(message)s"}},
            "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "precise"}},
            "root": {"level": "INFO", "handlers": ["console"]},
            "disable_existing_loggers": False,
        }
        (data_dir / "log.config").write_text(yaml.safe_dump(log_config, sort_keys=False))
        PY
        exec /start.py run
      SCRIPT
      ]
      portMappings = [
        { containerPort = 8008, protocol = "tcp" }
      ]
      environment = [
        { name = "SYNAPSE_SERVER_NAME", value = var.public_matrix_domain_name },
        { name = "SYNAPSE_REPORT_STATS", value = "no" },
        { name = "SYNAPSE_CONFIG_PATH", value = "/data/homeserver.yaml" },
        { name = "SYNAPSE_DB_HOST", value = aws_db_instance.matrix_synapse[0].address },
        { name = "SYNAPSE_RDS_CA_BUNDLE_URL", value = var.matrix_rds_ca_bundle_url },
        { name = "SYNAPSE_RDS_CA_BUNDLE_SHA256", value = var.matrix_rds_ca_bundle_sha256 },
      ]
      secrets = [
        {
          name      = "SYNAPSE_DB_PASSWORD"
          valueFrom = "${aws_db_instance.matrix_synapse[0].master_user_secret[0].secret_arn}:password::"
        },
        {
          name      = "SYNAPSE_SIGNING_KEY"
          valueFrom = aws_secretsmanager_secret.matrix_homeserver_signing_key.arn
        },
        {
          name      = "SYNAPSE_MACAROON_SECRET_KEY"
          valueFrom = aws_secretsmanager_secret.matrix_macaroon_secret_key.arn
        },
        {
          name      = "SYNAPSE_REGISTRATION_SHARED_SECRET"
          valueFrom = aws_secretsmanager_secret.matrix_registration_shared_secret.arn
        },
        {
          name      = "SYNAPSE_FORM_SECRET"
          valueFrom = aws_secretsmanager_secret.matrix_form_secret.arn
        },
      ]
      mountPoints = [{
        sourceVolume  = "synapse-data"
        containerPath = "/data"
        readOnly      = false
      }]
      healthCheck = {
        command     = ["CMD-SHELL", "python -c 'import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8008/_matrix/client/versions\", timeout=3).read()' || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 5
        startPeriod = 90
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.matrix_synapse[0].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = local.tags
}

resource "aws_ecs_service" "matrix_synapse" {
  count = var.enable_matrix_synapse ? 1 : 0

  name            = "${local.name_prefix}-matrix-synapse"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.matrix_synapse[0].arn
  desired_count   = var.enable_matrix_synapse && var.start_ecs_services && var.start_matrix_synapse_service ? var.matrix_synapse_desired_count : 0
  launch_type     = "FARGATE"

  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.matrix.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.matrix_client[0].arn
    container_name   = "synapse"
    container_port   = 8008
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
      condition     = !var.start_matrix_synapse_service || var.enable_matrix_backup
      error_message = "start_matrix_synapse_service requires enable_matrix_backup so RDS and EFS are protected before startup."
    }

    precondition {
      condition     = var.matrix_synapse_desired_count == 1
      error_message = "Monolithic Synapse requires matrix_synapse_desired_count == 1 until worker-mode topology is implemented."
    }

    precondition {
      condition     = !var.start_matrix_synapse_service || length(var.matrix_alarm_actions) > 0
      error_message = "Starting production Synapse requires at least one matrix_alarm_actions incident destination."
    }

    precondition {
      condition     = var.matrix_synapse_task_cpu >= 1024 && var.matrix_synapse_task_memory >= 2048
      error_message = "Production Synapse requires at least 1 vCPU and 2 GiB memory."
    }

    precondition {
      condition = !var.start_matrix_synapse_service || can(regex(
        "^${data.aws_caller_identity.current.account_id}\\.dkr\\.ecr\\.${data.aws_region.current.name}\\.amazonaws\\.com/${local.name_prefix}-runtime-grpc@sha256:[0-9a-f]{64}$",
        var.matrix_synapse_image,
      ))
      error_message = "matrix_synapse_image must be a digest-pinned hardened ECR image before production startup."
    }
  }

  depends_on = [
    aws_efs_mount_target.matrix_synapse,
    aws_db_instance.matrix_synapse,
  ]

  tags = local.tags
}
