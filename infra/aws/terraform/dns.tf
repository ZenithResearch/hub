locals {
  dns_enabled = var.domain_name != "" && var.route53_zone_id != ""
}

resource "aws_route53_record" "gateway_a" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = var.route53_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = var.enable_cloudfront ? aws_cloudfront_distribution.this[0].domain_name : aws_lb.gateway.dns_name
    zone_id                = var.enable_cloudfront ? aws_cloudfront_distribution.this[0].hosted_zone_id : aws_lb.gateway.zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "gateway_aaaa" {
  count   = local.dns_enabled ? 1 : 0
  zone_id = var.route53_zone_id
  name    = var.domain_name
  type    = "AAAA"

  alias {
    name                   = var.enable_cloudfront ? aws_cloudfront_distribution.this[0].domain_name : aws_lb.gateway.dns_name
    zone_id                = var.enable_cloudfront ? aws_cloudfront_distribution.this[0].hosted_zone_id : aws_lb.gateway.zone_id
    evaluate_target_health = false
  }
}

