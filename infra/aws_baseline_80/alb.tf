resource "aws_lb" "gateway" {
  name               = "${local.name_prefix}-alb"
  load_balancer_type = "application"
  internal           = false
  ip_address_type    = var.enable_dual_stack_public_edge ? "dualstack" : "ipv4"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  idle_timeout               = var.alb_idle_timeout_seconds
  drop_invalid_header_fields = true

  tags = merge(local.tags, { Name = "${local.name_prefix}-alb" })
}

resource "aws_lb_target_group" "gateway" {
  name        = "${local.name_prefix}-gateway"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200-399"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-gateway-tg" })
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.gateway.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = var.public_hub_domain_name != "" && var.enable_https_listener ? [1] : []

    content {
      type = "redirect"

      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  dynamic "default_action" {
    for_each = var.public_hub_domain_name == "" || !var.enable_https_listener ? [1] : []

    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.gateway.arn
    }
  }
}

resource "aws_acm_certificate" "gateway" {
  count = var.public_hub_domain_name != "" ? 1 : 0

  domain_name       = var.public_hub_domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-gateway-cert" })
}

resource "aws_lb_listener" "https" {
  count = var.public_hub_domain_name != "" && var.enable_https_listener ? 1 : 0

  load_balancer_arn = aws_lb.gateway.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.gateway[0].arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }
}
