from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IAC = ROOT / "infra/aws_baseline_80"


def test_agent_admin_ssm_document_accepts_only_fixed_actions() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")

    assert 'resource "aws_ssm_document" "hermes_cloud_agent_control"' in terraform
    assert 'allowedValues = ["enable", "disable", "restart", "status"]' in terraform
    assert "/usr/local/libexec/hermes-cloud-agent-control '{{ Action }}'" in terraform
    assert '"AWS-RunShellScript"' not in terraform
    for forbidden in ("Arguments", "Environment", "DocumentName"):
        assert f'"{forbidden}"' not in terraform


def test_agent_admin_task_role_cannot_start_sessions_or_select_documents() -> None:
    iam = (IAC / "iam.tf").read_text(encoding="utf-8")

    assert 'resource "aws_iam_role" "agent_admin_task"' in iam
    assert 'actions = ["ssm:SendCommand"]' in iam
    assert "aws_ssm_document.hermes_cloud_agent_control[0].arn" in iam
    assert "aws_instance.hermes_cloud_agent[0].arn" in iam
    assert "ssm:StartSession" not in iam
    assert "ssm:SendAutomationSignal" not in iam
    assert "ssm:*" not in iam


def test_agent_admin_service_is_private_single_replica_and_persistent() -> None:
    ecs = (IAC / "ecs.tf").read_text(encoding="utf-8")
    discovery = (IAC / "service_discovery.tf").read_text(encoding="utf-8")
    security_groups = (IAC / "security_groups.tf").read_text(encoding="utf-8")
    efs = (IAC / "efs.tf").read_text(encoding="utf-8")

    assert 'resource "aws_ecs_task_definition" "agent_admin"' in ecs
    assert 'resource "aws_ecs_service" "agent_admin"' in ecs
    assert 'command                = ["python", "-m", "services.agent_admin.main"]' in ecs
    assert "assign_public_ip = false" in ecs
    assert "agent_admin_desired_count" in ecs
    assert 'resource "aws_service_discovery_service" "agent_admin"' in discovery
    assert 'name = "agent-admin"' in discovery
    assert 'resource "aws_security_group" "agent_admin"' in security_groups
    assert "gateway_to_agent_admin_grpc" in security_groups
    assert 'resource "aws_efs_file_system" "agent_admin"' in efs
    assert re.search(r"encrypted\s+= true", efs)
    assert re.search(
        r"kms_key_id\s+= var\.hermes_cloud_agent_state_kms_key_arn", efs
    )
    agent_admin_efs = efs.split(
        'resource "aws_efs_file_system" "agent_admin"', 1
    )[1].split('resource "aws_efs_access_point" "agent_admin"', 1)[0]
    assert "prevent_destroy = true" in agent_admin_efs


def test_agent_admin_uses_private_ssm_endpoint_without_public_https_egress() -> None:
    endpoints = (IAC / "hermes_cloud_agent_endpoints.tf").read_text(encoding="utf-8")
    security_groups = (IAC / "security_groups.tf").read_text(encoding="utf-8")
    agent_admin = security_groups.split(
        'resource "aws_security_group" "agent_admin"', 1
    )[1]

    assert 'service_name        = "com.amazonaws.${var.aws_region}.ssm"' in endpoints
    assert 'toset(["ecr.api", "ecr.dkr", "logs"])' in endpoints
    assert 'service_name      = "com.amazonaws.${var.aws_region}.s3"' in endpoints
    assert "route_table_ids   = [aws_route_table.private.id]" in endpoints
    assert "private_dns_enabled = true" in endpoints
    assert "security_groups = [aws_security_group.hermes_cloud_agent_ssm_endpoint[0].id]" in agent_admin
    assert "prefix_list_ids = [aws_vpc_endpoint.hermes_cloud_agent_s3[0].prefix_list_id]" in agent_admin
    assert 'cidr_blocks = ["0.0.0.0/0"]' not in agent_admin


def test_agent_admin_compose_has_no_host_port() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    parsed = __import__("yaml").safe_load(compose)
    service = parsed["services"]["agent-admin"]

    assert service["command"] == ["python", "-m", "services.agent_admin.main"]
    assert service["expose"] == ["50054"]
    assert "ports" not in service
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]


def test_agent_admin_gateway_auth_is_dedicated_from_review_access() -> None:
    app = (ROOT / "services/gateway_http/app.py").read_text(encoding="utf-8")
    config = (ROOT / "libs/common/config.py").read_text(encoding="utf-8")
    ecs = (IAC / "ecs.tf").read_text(encoding="utf-8")
    secrets = (IAC / "secrets.tf").read_text(encoding="utf-8")

    agent_routes = app.split('@app.post("/v1/admin/agents/{profile_id}/register")', 1)[1].split(
        '@app.put("/v1/admin/review-auth/admin-token"', 1
    )[0]
    assert "_require_agent_admin(request)" in agent_routes
    assert "_require_review_access_admin(request)" not in agent_routes
    assert 'alias="AGENT_ADMIN_BEARER_TOKEN"' in config
    assert 'name      = "AGENT_ADMIN_BEARER_TOKEN"' in ecs
    variables = (IAC / "variables.tf").read_text(encoding="utf-8")

    assert 'resource "aws_secretsmanager_secret" "agent_admin_bearer_token"' in secrets
    assert 'variable "agent_admin_bearer_token_secret_ready"' in variables
    assert "var.agent_admin_bearer_token_secret_ready" in ecs
    assert "Populate the managed Agent Admin bearer secret out-of-band" in ecs
    assert 'data "aws_secretsmanager_secret_version"' not in secrets
