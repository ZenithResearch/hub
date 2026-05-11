provider "aws" {
  region = var.aws_region
}

# CloudFront + WAF (CLOUDFRONT scope) requires us-east-1.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

