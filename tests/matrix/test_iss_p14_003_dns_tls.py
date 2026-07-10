from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def read(rel: str) -> str:
    return (ROOT / rel).read_text()

def test_matrix_dns_tls_declares_direct_synapse_host_and_route53_alias():
    variables = read("infra/aws_baseline_80/variables.tf")
    dns_tls = read("infra/aws_baseline_80/matrix_dns_tls.tf")
    assert 'variable "public_matrix_domain_name"' in variables
    assert 'synapse.zenith-research.ca' in variables
    assert 'variable "matrix_hosted_zone_id"' in variables
    assert 'resource "aws_route53_record" "matrix_client"' in dns_tls
    assert 'aws_lb.gateway.dns_name' in dns_tls
    assert 'aws_lb.gateway.zone_id' in dns_tls

def test_matrix_tls_contract_reuses_existing_https_listener_without_duplicate_443_listener():
    variables = read("infra/aws_baseline_80/variables.tf")
    dns_tls = read("infra/aws_baseline_80/matrix_dns_tls.tf")
    assert 'resource "aws_acm_certificate" "matrix"' in dns_tls
    assert 'resource "aws_lb_listener" "matrix_https"' not in dns_tls
    assert 'resource "aws_lb_listener_rule" "matrix_https_host"' in dns_tls
    assert 'aws_lb_listener.https[0].arn' in dns_tls
    assert 'host_header' in dns_tls
    assert 'variable "enable_matrix_federation"' in variables
    assert 'variable "matrix_federation_allowed_cidr_blocks"' in variables
    assert 'resource "aws_lb_listener" "matrix_federation"' in dns_tls
    assert 'port              = 8448' in dns_tls


def test_matrix_tls_listener_supports_external_dns_validation_and_network_path():
    dns_tls = read("infra/aws_baseline_80/matrix_dns_tls.tf")
    security_groups = read("infra/aws_baseline_80/security_groups.tf")
    assert 'count = var.matrix_hosted_zone_id != "" && var.public_matrix_domain_name != "" ? 1 : 0' in dns_tls
    assert 'var.enable_matrix_https_listener' in dns_tls
    assert 'var.public_matrix_domain_name != "" && var.enable_matrix_federation' in dns_tls
    assert 'var.matrix_hosted_zone_id != "" && var.public_matrix_domain_name != "" && var.enable_matrix_federation' not in dns_tls
    assert 'var.matrix_hosted_zone_id != "" ? aws_acm_certificate_validation.matrix[0].certificate_arn : aws_acm_certificate.matrix[0].arn' in dns_tls
    assert 'matrix_federation_8448_explicit' in security_groups
    assert 'from_port        = 8448' in security_groups
    assert 'alb_to_matrix_client_http' in security_groups
    assert 'from_port   = 8008' in security_groups


def test_matrix_certificate_and_alb_dns_outputs_support_external_dns_operator():
    outputs = read("infra/aws_baseline_80/outputs.tf")
    assert 'output "matrix_certificate_dns_validation_records"' in outputs
    assert 'output "matrix_alb_dns_name"' in outputs

def test_matrix_tls_runbook_records_smoke_commands_and_no_production_overclaim():
    doc = read("docs/operations/matrix-dns-tls.md")
    assert 'dig +short synapse.zenith-research.ca' in doc
    assert 'openssl s_client -connect synapse.zenith-research.ca:443' in doc
    assert 'curl -fsS https://synapse.zenith-research.ca/_matrix/client/versions' in doc
    assert 'Do not claim production Synapse' in doc
