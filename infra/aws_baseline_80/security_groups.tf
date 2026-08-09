resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "ALB: public ingress; egress only to gateway tasks"
  vpc_id      = aws_vpc.this.id

  ingress {
    description      = "public_http"
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = var.enable_dual_stack_public_edge ? ["::/0"] : []
  }

  ingress {
    description      = "public_https"
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = var.enable_dual_stack_public_edge ? ["::/0"] : []
  }

  dynamic "ingress" {
    for_each = var.enable_matrix_federation ? [1] : []

    content {
      description      = "matrix_federation_8448_explicit"
      from_port        = 8448
      to_port          = 8448
      protocol         = "tcp"
      cidr_blocks      = var.matrix_federation_allowed_cidr_blocks
      ipv6_cidr_blocks = var.enable_dual_stack_public_edge ? var.matrix_federation_allowed_ipv6_cidr_blocks : []
    }
  }

  egress {
    description = "alb_to_matrix_client_http"
    from_port   = 8008
    to_port     = 8008
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "alb_to_gateway_and_mas_http"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  dynamic "egress" {
    for_each = var.enable_matrix_mas_public_edge ? [1] : []
    content {
      description = "alb_to_mas_health"
      from_port   = 8081
      to_port     = 8081
      protocol    = "tcp"
      cidr_blocks = aws_subnet.private[*].cidr_block
    }
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-alb-sg" })
}

resource "aws_security_group" "gateway" {
  name        = "${local.name_prefix}-gateway-sg"
  description = "gateway-http tasks: ingress only from ALB; egress only to runtime + DNS"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "alb_to_gateway_http"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "gateway_http_to_runtime_grpc"
    from_port   = 50051
    to_port     = 50051
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "gateway_to_queue_http"
    from_port   = 8081
    to_port     = 8081
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "gateway_to_cases_http"
    from_port   = 8083
    to_port     = 8083
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "gateway_to_eventbus_http"
    from_port   = 8082
    to_port     = 8082
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "gateway_to_llama_server_openai"
    from_port   = 3690
    to_port     = 3690
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "gateway_to_efs_clients_db"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  dynamic "egress" {
    for_each = var.enable_clients_postgres ? [1] : []

    content {
      description = "gateway_to_clients_postgres"
      from_port   = 5432
      to_port     = 5432
      protocol    = "tcp"
      cidr_blocks = aws_subnet.private[*].cidr_block
    }
  }

  # Required for Fargate image pulls/log delivery via NAT unless VPC endpoints are added.
  egress {
    description = "aws_control_plane_https"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # DNS to VPC resolver (best-effort; SGs can't express the resolver IP cleanly).
  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-gateway-sg" })
}

resource "aws_security_group" "runtime" {
  name        = "${local.name_prefix}-runtime-sg"
  description = "runtime-grpc tasks: ingress only from gateway; egress to sandbox + HTTPS + DNS"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "gateway_http_to_runtime_grpc"
    from_port       = 50051
    to_port         = 50051
    protocol        = "tcp"
    security_groups = [aws_security_group.gateway.id]
  }

  egress {
    description = "runtime_grpc_to_tool_sandbox"
    from_port   = 50052
    to_port     = 50052
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  # Qdrant Cloud is external and typically uses HTTPS 443. SGs cannot domain-allowlist;
  # this is a baseline allow to the internet on 443.
  egress {
    description = "https_egress"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-runtime-sg" })
}

resource "aws_security_group" "sandbox" {
  name        = "${local.name_prefix}-sandbox-sg"
  description = "tool-sandbox tasks: ingress only from runtime; no general egress (baseline)"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "runtime_grpc_to_tool_sandbox"
    from_port       = 50052
    to_port         = 50052
    protocol        = "tcp"
    security_groups = [aws_security_group.runtime.id]
  }

  # Keep sandbox egress minimal. Note: this does not provide strong network isolation for tool subprocesses,
  # but reduces accidental outbound connectivity in the baseline.
  # HTTPS egress is required for Fargate image pulls/log delivery via NAT unless VPC endpoints are added.
  egress {
    description = "aws_control_plane_https"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-sandbox-sg" })
}

# Queue tasks: gRPC ingress from internal services; HTTP ingress from internal services;
# EFS egress for SQLite persistence; DNS egress.
resource "aws_security_group" "queue" {
  name        = "${local.name_prefix}-queue-sg"
  description = "queue tasks: gRPC 50053 + HTTP 8081 ingress from VPC; EFS + DNS egress"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "grpc_from_vpc"
    from_port   = 50053
    to_port     = 50053
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  ingress {
    description = "http_from_vpc"
    from_port   = 8081
    to_port     = 8081
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  egress {
    description = "efs_nfs"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  # Required for Fargate image pulls/log delivery via NAT unless VPC endpoints are added.
  egress {
    description = "aws_control_plane_https"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-queue-sg" })
}

resource "aws_security_group" "cases" {
  name        = "${local.name_prefix}-cases-sg"
  description = "cases tasks: HTTP 8083 ingress from VPC; EFS + HTTPS + DNS egress"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "http_from_vpc"
    from_port   = 8083
    to_port     = 8083
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  egress {
    description = "efs_nfs"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "aws_control_plane_https"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-cases-sg" })
}

resource "aws_security_group" "eventbus" {
  name        = "${local.name_prefix}-eventbus-sg"
  description = "eventbus tasks: HTTP 8082 ingress from VPC; HTTPS + DNS egress"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "http_from_vpc"
    from_port   = 8082
    to_port     = 8082
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  egress {
    description = "aws_control_plane_https"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-eventbus-sg" })
}

resource "aws_security_group" "frank" {
  name        = "${local.name_prefix}-frank-sg"
  description = "Frank dispatcher tasks: egress to queue/eventbus/cases/gateway/STT; no ingress"
  vpc_id      = aws_vpc.this.id

  egress {
    description = "frank_to_queue_http"
    from_port   = 8081
    to_port     = 8081
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "frank_to_eventbus_http"
    from_port   = 8082
    to_port     = 8082
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "frank_to_cases_http"
    from_port   = 8083
    to_port     = 8083
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "frank_to_stt_http"
    from_port   = 8765
    to_port     = 8765
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "frank_to_llama_server_openai"
    from_port   = 3690
    to_port     = 3690
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "efs_nfs"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "https_egress"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-frank-sg" })
}

resource "aws_security_group" "stt_http" {
  name        = "${local.name_prefix}-stt-http-sg"
  description = "STT HTTP tasks: HTTP 8765 ingress from VPC; EFS + HTTPS + DNS egress"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "http_from_vpc"
    from_port   = 8765
    to_port     = 8765
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  egress {
    description = "efs_nfs"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }

  egress {
    description = "https_egress"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-stt-http-sg" })
}

resource "aws_security_group" "llama_server" {
  name        = "${local.name_prefix}-llama-server-sg"
  description = "Internal llama-server Qwen endpoint"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "openai_compatible_from_vpc"
    from_port   = 3690
    to_port     = 3690
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  egress {
    description = "efs_nfs"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  egress {
    description = "llama_server_smoke_test"
    from_port   = 3690
    to_port     = 3690
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.this.cidr_block]
  }

  egress {
    description = "aws_control_plane_https"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_udp"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "dns_tcp"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description      = "live_ipv6_default_egress_preserved_during_import"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-llama-server-sg" })
}

resource "aws_security_group" "clients_postgres" {
  count = var.enable_clients_postgres ? 1 : 0

  name        = "${local.name_prefix}-clients-postgres-sg"
  description = "clients Postgres: private ingress only from gateway tasks"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "gateway_to_clients_postgres"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.gateway.id]
  }

  tags = merge(local.tags, { Name = "${local.name_prefix}-clients-postgres-sg" })
}

