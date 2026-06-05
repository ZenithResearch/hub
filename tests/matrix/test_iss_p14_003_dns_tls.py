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

def test_matrix_tls_contract_uses_acm_https_and_explicit_federation_8448():
    variables = read("infra/aws_baseline_80/variables.tf")
    dns_tls = read("infra/aws_baseline_80/matrix_dns_tls.tf")
    assert 'resource "aws_acm_certificate" "matrix"' in dns_tls
    assert 'resource "aws_lb_listener" "matrix_https"' in dns_tls
    assert 'port              = 443' in dns_tls
    assert 'variable "enable_matrix_federation"' in variables
    assert 'variable "matrix_federation_allowed_cidr_blocks"' in variables
    assert 'resource "aws_lb_listener" "matrix_federation"' in dns_tls
    assert 'port              = 8448' in dns_tls

def test_matrix_tls_runbook_records_smoke_commands_and_no_production_overclaim():
    doc = read("docs/operations/matrix-dns-tls.md")
    assert 'dig +short synapse.zenith-research.ca' in doc
    assert 'openssl s_client -connect synapse.zenith-research.ca:443' in doc
    assert 'curl -fsS https://synapse.zenith-research.ca/_matrix/client/versions' in doc
    assert 'Do not claim production Synapse' in doc
