from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gateway_mounts_frank_execution_read_only_for_hubfs() -> None:
    ecs = (ROOT / "infra/aws_baseline_80/ecs.tf").read_text()
    efs = (ROOT / "infra/aws_baseline_80/efs.tf").read_text()
    iam = (ROOT / "infra/aws_baseline_80/iam.tf").read_text()

    gateway_block = ecs.split('resource "aws_ecs_task_definition" "gateway" {', 1)[1].split(
        'resource "aws_ecs_task_definition" "runtime" {', 1
    )[0]

    assert 'name = "frank-execution-data"' in gateway_block
    assert "file_system_id     = aws_efs_file_system.frank.id" in gateway_block
    assert "access_point_id = aws_efs_access_point.frank_execution.id" in gateway_block
    assert 'sourceVolume  = "frank-execution-data"' in gateway_block
    assert 'containerPath = "/data/frank_execution"' in gateway_block
    assert "readOnly      = true" in gateway_block
    assert '{ name = "HUBFS_ALLOWED_ROOTS", value = "/data:/app/base/ops/processes" }' in gateway_block

    frank_execution_ap = efs.split('resource "aws_efs_access_point" "frank_execution" {', 1)[1].split(
        '# EFS security group', 1
    )[0]
    assert 'path = "/data/frank_execution"' in frank_execution_ap

    efs_frank_block = efs.split('resource "aws_security_group" "efs_frank" {', 1)[1].split(
        'resource "aws_security_group" "efs_gateway" {', 1
    )[0]
    assert 'description     = "nfs_from_gateway_tasks"' in efs_frank_block
    assert "security_groups = [aws_security_group.gateway.id]" in efs_frank_block

    gateway_efs_policy = iam.split('data "aws_iam_policy_document" "gateway_efs" {', 1)[1].split(
        'resource "aws_iam_role_policy" "gateway_efs" {', 1
    )[0]
    assert "resources = [aws_efs_file_system.gateway.arn]" in gateway_efs_policy
    assert "resources = [aws_efs_file_system.frank.arn]" in gateway_efs_policy
    assert 'variable = "elasticfilesystem:AccessPointArn"' in gateway_efs_policy
    assert "values   = [aws_efs_access_point.frank_execution.arn]" in gateway_efs_policy

    frank_mount_statement = gateway_efs_policy.split("resources = [aws_efs_file_system.frank.arn]", 1)[0].rsplit(
        "statement {", 1
    )[1]
    assert "ClientMount" in frank_mount_statement
    assert "ClientRootAccess" in frank_mount_statement
    assert "ClientWrite" not in frank_mount_statement


def test_frank_publishes_process_docs_as_gateway_hubfs_paths() -> None:
    ecs = (ROOT / "infra/aws_baseline_80/ecs.tf").read_text()
    frank_block = ecs.split('resource "aws_ecs_task_definition" "frank" {', 1)[1].split(
        'resource "aws_ecs_service" "frank" {', 1
    )[0]

    assert '{ name = "TERMINAL_CWD", value = "/app" }' in frank_block
    assert '{ name = "PROCESS_HUBFS_ROOT", value = "/app/base/ops/processes" }' in frank_block
