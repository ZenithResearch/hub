resource "aws_security_group" "hermes_cloud_agent_ssm_endpoint" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  name        = "${local.name_prefix}-hermes-ssm-endpoint-sg"
  description = "Private SSM API endpoint for the Hermes Agent Admin task"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-hermes-ssm-endpoint-sg" })
}

resource "aws_security_group_rule" "hermes_cloud_agent_ssm_endpoint_ingress" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  type                     = "ingress"
  description              = "https_from_agent_admin"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.agent_admin[0].id
  security_group_id        = aws_security_group.hermes_cloud_agent_ssm_endpoint[0].id
}

resource "aws_vpc_endpoint" "hermes_cloud_agent_ssm" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.aws_region}.ssm"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.hermes_cloud_agent_ssm_endpoint[0].id]

  tags = merge(local.tags, { Name = "${local.name_prefix}-hermes-ssm-endpoint" })
}

resource "aws_vpc_endpoint" "hermes_cloud_agent_fargate" {
  for_each = var.enable_hermes_cloud_agent ? toset(["ecr.api", "ecr.dkr", "logs"]) : toset([])

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.hermes_cloud_agent_ssm_endpoint[0].id]

  tags = merge(local.tags, {
    Name = "${local.name_prefix}-hermes-${replace(each.key, ".", "-")}-endpoint"
  })
}

resource "aws_vpc_endpoint" "hermes_cloud_agent_s3" {
  count = var.enable_hermes_cloud_agent ? 1 : 0

  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = merge(local.tags, { Name = "${local.name_prefix}-hermes-s3-endpoint" })
}
