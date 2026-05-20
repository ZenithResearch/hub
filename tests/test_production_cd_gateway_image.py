from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_production_cd_accepts_gateway_image_tag_override():
    workflow = (ROOT / ".github/workflows/production-cd.yml").read_text()
    helper = (ROOT / "scripts/prod_terraform_cd.sh").read_text()

    assert "gateway_image_tag:" in workflow
    assert "GATEWAY_IMAGE_TAG: ${{ inputs.gateway_image_tag }}" in workflow
    assert "${GATEWAY_IMAGE_TAG" in helper
    assert '-var="gateway_image_tag=$GATEWAY_IMAGE_TAG"' in helper


def test_manual_gateway_image_build_workflow_pushes_to_gateway_ecr_only():
    workflow_path = ROOT / ".github/workflows/gateway-image.yml"
    assert workflow_path.exists()
    workflow = workflow_path.read_text()

    assert "workflow_dispatch:" in workflow
    assert "aws-actions/configure-aws-credentials" in workflow
    assert "aws-actions/amazon-ecr-login" in workflow
    assert "docker/build-push-action" in workflow
    assert "zenith-hub-prod-gateway-http" in workflow
    assert "push: true" in workflow
    assert "tags:" in workflow
    assert "linux/amd64" in workflow


def test_gateway_image_override_does_not_roll_eventbus():
    ecs_tf = (ROOT / "infra/aws_baseline_80/ecs.tf").read_text()
    eventbus_block = ecs_tf.split('resource "aws_ecs_task_definition" "eventbus" {', 1)[1].split(
        'resource "aws_ecs_service" "eventbus" {', 1
    )[0]

    assert 'image     = "${aws_ecr_repository.gateway.repository_url}:${var.image_tag}"' in eventbus_block
    assert "local.gateway_image_tag" not in eventbus_block
