# EFS file system for queue SQLite persistence.
# SQLite requires a single persistent volume — Fargate containers are ephemeral,
# so EFS (managed NFS) is the correct mount for single-writer WAL-mode SQLite.

resource "aws_efs_file_system" "queue" {
  creation_token   = "${local.name_prefix}-queue-data"
  encrypted        = true
  throughput_mode  = "bursting"
  performance_mode = "generalPurpose"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-queue-efs" })
}

# EFS file system for cases SQLite persistence.
# Keep cases desired_count at 1 while cases.db is SQLite-backed.
resource "aws_efs_file_system" "cases" {
  creation_token   = "${local.name_prefix}-cases-data"
  encrypted        = true
  throughput_mode  = "bursting"
  performance_mode = "generalPurpose"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-cases-efs" })
}

# EFS file system for Frank dispatcher execution artifacts.
resource "aws_efs_file_system" "frank" {
  creation_token   = "${local.name_prefix}-frank-data"
  encrypted        = true
  throughput_mode  = "bursting"
  performance_mode = "generalPurpose"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-frank-efs" })
}

# EFS file system for gateway-http Review SDK auth clients registry.
# Keep gateway desired_count at 1 while clients.db is SQLite-backed.
resource "aws_efs_file_system" "gateway" {
  creation_token   = "${local.name_prefix}-gateway-data"
  encrypted        = true
  throughput_mode  = "bursting"
  performance_mode = "generalPurpose"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-gateway-efs" })
}

resource "aws_efs_mount_target" "gateway" {
  count           = length(aws_subnet.private)
  file_system_id  = aws_efs_file_system.gateway.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs_gateway.id]
}

resource "aws_efs_access_point" "gateway" {
  file_system_id = aws_efs_file_system.gateway.id

  posix_user {
    uid = var.gateway_data_uid
    gid = var.gateway_data_gid
  }

  root_directory {
    path = "/data"
    creation_info {
      owner_uid   = var.gateway_data_uid
      owner_gid   = var.gateway_data_gid
      permissions = "750"
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-gateway-ap" })
}

# One mount target per private subnet so any AZ can reach the file system.
resource "aws_efs_mount_target" "queue" {
  count           = length(aws_subnet.private)
  file_system_id  = aws_efs_file_system.queue.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs_queue.id]
}

resource "aws_efs_mount_target" "cases" {
  count           = length(aws_subnet.private)
  file_system_id  = aws_efs_file_system.cases.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs_cases.id]
}

resource "aws_efs_mount_target" "frank" {
  count           = length(aws_subnet.private)
  file_system_id  = aws_efs_file_system.frank.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs_frank.id]
}

# Access point scopes the queue container to /data with fixed UID/GID 1000.
resource "aws_efs_access_point" "queue" {
  file_system_id = aws_efs_file_system.queue.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/data"
    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "750"
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-queue-ap" })
}

resource "aws_efs_access_point" "cases" {
  file_system_id = aws_efs_file_system.cases.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/data"
    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "750"
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-cases-ap" })
}

resource "aws_efs_access_point" "frank" {
  file_system_id = aws_efs_file_system.frank.id

  posix_user {
    uid = var.gateway_data_uid
    gid = var.gateway_data_gid
  }

  root_directory {
    path = "/data"
    creation_info {
      owner_uid   = var.gateway_data_uid
      owner_gid   = var.gateway_data_gid
      permissions = "750"
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-frank-ap" })
}

# Gateway exposes Frank-produced runtime artifacts through HubFS at /data/frank_execution.
resource "aws_efs_access_point" "frank_execution" {
  file_system_id = aws_efs_file_system.frank.id

  posix_user {
    uid = var.gateway_data_uid
    gid = var.gateway_data_gid
  }

  root_directory {
    path = "/data/frank_execution"
    creation_info {
      owner_uid   = var.gateway_data_uid
      owner_gid   = var.gateway_data_gid
      permissions = "750"
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-frank-execution-ap" })
}

# EFS security group: allows NFS (2049) ingress from queue tasks only.
resource "aws_security_group" "efs_queue" {
  name        = "${local.name_prefix}-efs-queue-sg"
  description = "EFS mount targets for queue: NFS ingress from queue tasks only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "nfs_from_queue_tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.queue.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-efs-queue-sg" })
}

resource "aws_security_group" "efs_cases" {
  name        = "${local.name_prefix}-efs-cases-sg"
  description = "EFS mount targets for cases: NFS ingress from cases tasks only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "nfs_from_cases_tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.cases.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-efs-cases-sg" })
}

resource "aws_security_group" "efs_frank" {
  name        = "${local.name_prefix}-efs-frank-sg"
  description = "EFS mount targets for Frank: NFS ingress from Frank, STT, llama-server, and Gateway tasks"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "nfs_from_frank_tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.frank.id]
  }

  ingress {
    description     = "nfs_from_stt_http_tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.stt_http.id]
  }

  ingress {
    description     = "nfs_from_llama_server_tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.llama_server.id]
  }

  ingress {
    description     = "nfs_from_gateway_tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.gateway.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-efs-frank-sg" })
}

resource "aws_security_group" "efs_gateway" {
  name        = "${local.name_prefix}-efs-gateway-sg"
  description = "EFS mount targets for gateway: NFS ingress from gateway tasks only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "nfs_from_gateway_tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.gateway.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-efs-gateway-sg" })
}
