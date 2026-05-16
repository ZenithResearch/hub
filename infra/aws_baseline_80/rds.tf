resource "aws_db_subnet_group" "clients" {
  count = var.enable_clients_postgres ? 1 : 0

  name       = "${local.name_prefix}-clients-db-subnets"
  subnet_ids = aws_subnet.private[*].id

  tags = merge(local.tags, { Name = "${local.name_prefix}-clients-db-subnets" })
}

resource "aws_db_instance" "clients" {
  count = var.enable_clients_postgres ? 1 : 0

  identifier = "${local.name_prefix}-clients"

  engine         = "postgres"
  engine_version = var.clients_postgres_engine_version
  instance_class = var.clients_postgres_instance_class

  allocated_storage     = var.clients_postgres_allocated_storage_gb
  max_allocated_storage = var.clients_postgres_max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.clients_postgres_database_name
  username = var.clients_postgres_username

  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.clients[0].name
  vpc_security_group_ids = [aws_security_group.clients_postgres[0].id]
  publicly_accessible    = false

  backup_retention_period   = var.clients_postgres_backup_retention_days
  backup_window             = var.clients_postgres_backup_window
  maintenance_window        = var.clients_postgres_maintenance_window
  deletion_protection       = var.clients_postgres_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name_prefix}-clients-final"

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = merge(local.tags, { Name = "${local.name_prefix}-clients" })
}
