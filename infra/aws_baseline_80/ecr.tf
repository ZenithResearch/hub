resource "aws_ecr_repository" "gateway" {
  name                 = "${local.name_prefix}-gateway-http"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_repository" "runtime" {
  name                 = "${local.name_prefix}-runtime-grpc"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_repository" "sandbox" {
  name                 = "${local.name_prefix}-tool-sandbox"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_repository" "queue" {
  name                 = "${local.name_prefix}-queue"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

