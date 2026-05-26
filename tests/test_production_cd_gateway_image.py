from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_production_cd_workflow_is_removed_but_local_helper_keeps_image_overrides():
    assert not (ROOT / ".github/workflows/production-cd.yml").exists()

    helper = (ROOT / "scripts/prod_terraform_cd.sh").read_text()
    assert "${GATEWAY_IMAGE_TAG:?" in helper
    assert "${EVENTBUS_IMAGE_TAG:?" in helper
    assert "${CASES_IMAGE_TAG:?" in helper
    assert "${FRANK_IMAGE_TAG:?" in helper
    assert "${STT_IMAGE_TAG:?" in helper
    assert "gateway-admin-queue-case-20260518003135-5ae0998" not in helper
    assert "native-timeout-hotfix-20260519004401" not in helper
    assert "frank-stt-backoff-hotfix-20260519190823" not in helper
    assert "stt-cache-hotfix-20260519013103" not in helper
    assert '-var="gateway_image_tag=$GATEWAY_IMAGE_TAG"' in helper
    assert '-var="eventbus_image_tag=$EVENTBUS_IMAGE_TAG"' in helper
    assert '-var="cases_image_tag=$CASES_IMAGE_TAG"' in helper
    assert '-var="frank_image_tag=$FRANK_IMAGE_TAG"' in helper
    assert '-var="stt_image_tag=$STT_IMAGE_TAG"' in helper


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

    assert 'image     = "${aws_ecr_repository.gateway.repository_url}:${local.eventbus_image_tag}"' in eventbus_block
    assert "local.gateway_image_tag" not in eventbus_block
