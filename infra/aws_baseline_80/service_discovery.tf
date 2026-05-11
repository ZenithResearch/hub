resource "aws_service_discovery_private_dns_namespace" "this" {
  name        = local.cloudmap_namespace
  description = "Agent Platform internal discovery"
  vpc         = aws_vpc.this.id

  tags = local.tags
}

resource "aws_service_discovery_service" "runtime" {
  name = "runtime-grpc"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"

    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = local.tags
}

resource "aws_service_discovery_service" "sandbox" {
  name = "tool-sandbox"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"

    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = local.tags
}

resource "aws_service_discovery_service" "queue" {
  name = "queue"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.this.id
    routing_policy = "MULTIVALUE"

    dns_records {
      type = "A"
      ttl  = 10
    }
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = local.tags
}

