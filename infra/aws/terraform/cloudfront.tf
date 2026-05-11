data "aws_cloudfront_cache_policy" "caching_disabled" {
  count = var.enable_cloudfront ? 1 : 0
  name  = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  count = var.enable_cloudfront ? 1 : 0
  name  = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "this" {
  count = var.enable_cloudfront ? 1 : 0

  enabled         = true
  is_ipv6_enabled = true
  comment         = "${local.name_prefix} gateway distribution"

  origin {
    domain_name = aws_lb.gateway.dns_name
    origin_id   = "alb-origin"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = var.enable_https ? "https-only" : "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]

      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = "alb-origin"
    viewer_protocol_policy = "redirect-to-https"

    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD", "OPTIONS"]

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled[0].id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host[0].id

    compress = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = (var.domain_name == "" || var.cloudfront_acm_cert_arn == "")
    acm_certificate_arn            = (var.domain_name != "" && var.cloudfront_acm_cert_arn != "") ? var.cloudfront_acm_cert_arn : null
    ssl_support_method             = (var.domain_name != "" && var.cloudfront_acm_cert_arn != "") ? "sni-only" : null
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  aliases = (var.domain_name != "" && var.cloudfront_acm_cert_arn != "") ? [var.domain_name] : []

  web_acl_id = aws_wafv2_web_acl.cloudfront[0].arn

  tags = local.tags
}

