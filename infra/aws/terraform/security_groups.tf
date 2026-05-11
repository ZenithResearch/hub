data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "ALB ingress from the internet or CloudFront; egress to gateway tasks"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-alb-sg" })
}

# Ingress to ALB: either open to the world (simplest) or restricted to CloudFront.
resource "aws_vpc_security_group_ingress_rule" "alb_http_world" {
  count             = (var.enable_cloudfront && var.allow_cloudfront_only) ? 0 : 1
  security_group_id = aws_security_group.alb.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
  description       = "http_from_internet"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https_world" {
  count             = (var.enable_https && !(var.enable_cloudfront && var.allow_cloudfront_only)) ? 1 : 0
  security_group_id = aws_security_group.alb.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
  description       = "https_from_internet"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_cloudfront" {
  count             = (var.enable_cloudfront && var.allow_cloudfront_only) ? 1 : 0
  security_group_id = aws_security_group.alb.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
  description       = "http_from_cloudfront_origin_facing"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https_cloudfront" {
  count             = (var.enable_cloudfront && var.allow_cloudfront_only && var.enable_https) ? 1 : 0
  security_group_id = aws_security_group.alb.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
  description       = "https_from_cloudfront_origin_facing"
}

resource "aws_security_group" "gateway" {
  name        = "${local.name_prefix}-gateway-sg"
  description = "gateway-http tasks: ingress only from ALB; egress open (needed for DNS + service calls)"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-gateway-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "gateway_from_alb" {
  security_group_id            = aws_security_group.gateway.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  description                  = "alb_to_gateway_http"
}

resource "aws_security_group" "runtime" {
  name        = "${local.name_prefix}-runtime-sg"
  description = "runtime-grpc tasks: ingress only from gateway"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-runtime-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "runtime_from_gateway" {
  security_group_id            = aws_security_group.runtime.id
  referenced_security_group_id = aws_security_group.gateway.id
  ip_protocol                  = "tcp"
  from_port                    = 50051
  to_port                      = 50051
  description                  = "gateway_http_to_runtime_grpc"
}

resource "aws_security_group" "tool_sandbox" {
  name        = "${local.name_prefix}-tool-sg"
  description = "tool-sandbox tasks: ingress only from runtime"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = "${local.name_prefix}-tool-sg" })
}

resource "aws_vpc_security_group_ingress_rule" "tool_from_runtime" {
  security_group_id            = aws_security_group.tool_sandbox.id
  referenced_security_group_id = aws_security_group.runtime.id
  ip_protocol                  = "tcp"
  from_port                    = 50052
  to_port                      = 50052
  description                  = "runtime_grpc_to_tool_sandbox"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_gateway" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.gateway.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  description                  = "alb_to_gateway_http"
}

resource "aws_vpc_security_group_egress_rule" "gateway_all" {
  security_group_id = aws_security_group.gateway.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "gateway_egress_all"
}

resource "aws_vpc_security_group_egress_rule" "runtime_all" {
  security_group_id = aws_security_group.runtime.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "runtime_egress_all"
}

resource "aws_vpc_security_group_egress_rule" "tool_all" {
  security_group_id = aws_security_group.tool_sandbox.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  description       = "tool_egress_all"
}

