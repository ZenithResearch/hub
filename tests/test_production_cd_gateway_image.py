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


def test_manual_gateway_image_build_workflow_uses_cd_reusable_local_script():
    workflow_path = ROOT / ".github/workflows/gateway-image.yml"
    assert workflow_path.exists()
    workflow = workflow_path.read_text()

    assert "workflow_dispatch:" in workflow
    assert "aws-actions/configure-aws-credentials" in workflow
    assert "scripts/prod_build_image.sh" in workflow
    assert "zenith-hub-prod-gateway-http" in workflow
    assert "DOCKER_PLATFORM: linux/amd64" in workflow
    assert "prod_terraform_cd.sh" in workflow


def test_gateway_main_cd_builds_after_green_main_ci_and_uses_narrow_ecs_deploy():
    workflow_path = ROOT / ".github/workflows/gateway-main-cd.yml"
    assert workflow_path.exists()
    workflow = workflow_path.read_text()

    assert "workflow_run:" in workflow
    assert "workflows: [\"CI\"]" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "branches: [main]" in workflow
    assert "environment: production" in workflow
    assert "aws-actions/configure-aws-credentials" in workflow
    assert "scripts/prod_build_image.sh" in workflow
    assert "scripts/prod_deploy_gateway_image.sh" in workflow
    assert "zenith-hub-prod-gateway-http" in workflow
    assert "HUB_HEALTH_URL: https://hub.zenith-research.ca/health" in workflow


def test_gateway_narrow_deploy_script_updates_only_gateway_service_image():
    script = (ROOT / "scripts/prod_deploy_gateway_image.sh").read_text()

    assert "${IMAGE_TAG:?" in script
    assert "ECS_CLUSTER:=zenith-hub-prod-cluster" in script
    assert "ECS_SERVICE:=zenith-hub-prod-gateway-http" in script
    assert "ecr describe-images" in script
    assert "ecs describe-services" in script
    assert "ecs describe-task-definition" in script
    assert "ecs register-task-definition" in script
    assert "ecs update-service" in script
    assert "ecs wait services-stable" in script
    assert "HUB_HEALTH_URL:=https://hub.zenith-research.ca/health" in script
    assert "terraform apply" not in script
    assert "prod_terraform_cd.sh" not in script


def test_prod_build_image_script_builds_only_and_leaves_deploy_to_terraform():
    script = (ROOT / "scripts/prod_build_image.sh").read_text()

    assert "docker \"${BUILD_ARGS[@]}\"" in script
    assert "--push" in script
    assert "PUSH_IMAGE" in script
    assert "ECR_REPOSITORY:=zenith-hub-prod-gateway-http" in script
    assert "DOCKER_PLATFORM:=linux/amd64" in script
    assert "Refusing to build a production image from a dirty worktree" in script
    assert "prod_terraform_cd.sh" in script
    assert "terraform apply" not in script
    assert "aws ecs update-service" not in script


def test_gateway_image_override_does_not_roll_eventbus():
    ecs_tf = (ROOT / "infra/aws_baseline_80/ecs.tf").read_text()
    eventbus_block = ecs_tf.split('resource "aws_ecs_task_definition" "eventbus" {', 1)[1].split(
        'resource "aws_ecs_service" "eventbus" {', 1
    )[0]

    assert 'image     = "${aws_ecr_repository.gateway.repository_url}:${local.eventbus_image_tag}"' in eventbus_block
    assert "local.gateway_image_tag" not in eventbus_block
