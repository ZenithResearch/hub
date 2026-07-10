# Matrix/Synapse public DNS and TLS contract for PRP-PR-014 / ISS-P14-003.
# This file intentionally declares the production-facing contract without
# claiming that Synapse has been deployed or smoke-tested.

resource "aws_acm_certificate" "matrix" {
  count = var.public_matrix_domain_name != "" ? 1 : 0

  domain_name       = var.public_matrix_domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-cert" })
}

resource "aws_route53_record" "matrix_cert_validation" {
  for_each = var.matrix_hosted_zone_id != "" && var.public_matrix_domain_name != "" ? {
    for dvo in aws_acm_certificate.matrix[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.matrix_hosted_zone_id
}

resource "aws_acm_certificate_validation" "matrix" {
  count = var.matrix_hosted_zone_id != "" && var.public_matrix_domain_name != "" ? 1 : 0

  certificate_arn         = aws_acm_certificate.matrix[0].arn
  validation_record_fqdns = [for record in aws_route53_record.matrix_cert_validation : record.fqdn]
}

resource "aws_route53_record" "matrix_client" {
  count = var.matrix_hosted_zone_id != "" && var.public_matrix_domain_name != "" ? 1 : 0

  name    = var.public_matrix_domain_name
  type    = "A"
  zone_id = var.matrix_hosted_zone_id

  alias {
    evaluate_target_health = true
    name                   = aws_lb.gateway.dns_name
    zone_id                = aws_lb.gateway.zone_id
  }
}

resource "aws_route53_record" "matrix_client_ipv6" {
  count = var.matrix_hosted_zone_id != "" && var.public_matrix_domain_name != "" && var.enable_dual_stack_public_edge ? 1 : 0

  name    = var.public_matrix_domain_name
  type    = "AAAA"
  zone_id = var.matrix_hosted_zone_id

  alias {
    evaluate_target_health = true
    name                   = aws_lb.gateway.dns_name
    zone_id                = aws_lb.gateway.zone_id
  }
}

resource "aws_security_group" "matrix" {
  name        = "${local.name_prefix}-matrix-sg"
  description = "Synapse Matrix host: explicit client HTTPS and federation ingress"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "alb_to_matrix_client_http"
    from_port       = 8008
    to_port         = 8008
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }


  egress {
    description = "aws_control_plane_https"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "private_postgres"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "private_efs"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "vpc_dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "vpc_dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-sg" })
}

resource "aws_lb_target_group" "matrix_client" {
  count = var.public_matrix_domain_name != "" ? 1 : 0

  name        = "${local.name_prefix}-matrix-client"
  port        = 8008
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  health_check {
    enabled = true
    path    = "/_matrix/client/versions"
    matcher = "200-399"
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-matrix-client-tg" })
}

resource "aws_lb_listener_certificate" "matrix_https" {
  count = var.public_matrix_domain_name != "" && var.public_hub_domain_name != "" && var.enable_https_listener && var.enable_matrix_https_listener ? 1 : 0

  listener_arn    = aws_lb_listener.https[0].arn
  certificate_arn = var.matrix_hosted_zone_id != "" ? aws_acm_certificate_validation.matrix[0].certificate_arn : aws_acm_certificate.matrix[0].arn
}

resource "aws_lb_listener_rule" "matrix_https_host" {
  count = var.public_matrix_domain_name != "" && var.public_hub_domain_name != "" && var.enable_https_listener && var.enable_matrix_https_listener ? 1 : 0

  listener_arn = aws_lb_listener.https[0].arn
  priority     = var.matrix_https_listener_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.matrix_client[0].arn
  }

  condition {
    host_header {
      values = [var.public_matrix_domain_name]
    }
  }
}

resource "aws_lb_listener" "matrix_federation" {
  count = var.public_matrix_domain_name != "" && var.enable_matrix_federation ? 1 : 0

  load_balancer_arn = aws_lb.gateway.arn
  port              = 8448
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.matrix_hosted_zone_id != "" ? aws_acm_certificate_validation.matrix[0].certificate_arn : aws_acm_certificate.matrix[0].arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.matrix_client[0].arn
  }
}
