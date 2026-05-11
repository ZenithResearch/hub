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

# One mount target per private subnet so any AZ can reach the file system.
resource "aws_efs_mount_target" "queue" {
  count           = length(aws_subnet.private)
  file_system_id  = aws_efs_file_system.queue.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs_queue.id]
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
