#!/usr/bin/env bash
set -euo pipefail

# Production Terraform CD helper. Designed for GitHub Actions OIDC sessions and
# local operator use. It never supplies AWS credentials itself and expects the
# caller to have already authenticated.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${AWS_REGION:=us-east-1}"
: "${TERRAFORM_DIR:=infra/aws_baseline_80}"
: "${TERRAFORM_PLAN_PATH:=${RUNNER_TEMP:-/tmp}/hub-prod.tfplan}"
: "${TERRAFORM_PLAN_TEXT:=${RUNNER_TEMP:-/tmp}/hub-prod-plan.txt}"
: "${PROD_TFVARS_PATH:?set PROD_TFVARS_PATH to the production terraform.tfvars path}"
: "${IMAGE_TAG:?set IMAGE_TAG to the current shared runtime/queue/sandbox image tag unless intentionally rolling those services}"
: "${GATEWAY_IMAGE_TAG:?set GATEWAY_IMAGE_TAG to the intended gateway-http image tag}"
: "${EVENTBUS_IMAGE_TAG:?set EVENTBUS_IMAGE_TAG to the current live eventbus image tag unless intentionally rolling eventbus}"
: "${CASES_IMAGE_TAG:?set CASES_IMAGE_TAG to the current live cases image tag unless intentionally rolling cases}"
: "${FRANK_IMAGE_TAG:?set FRANK_IMAGE_TAG to the intended/current Frank image tag}"
: "${STT_IMAGE_TAG:?set STT_IMAGE_TAG to the current live STT image tag unless intentionally rolling STT}"

ACTION="${1:-plan}"
if [[ "$ACTION" != "plan" && "$ACTION" != "apply" ]]; then
  echo "usage: $0 [plan|apply]" >&2
  exit 2
fi

if [[ ! -f "$PROD_TFVARS_PATH" ]]; then
  echo "PROD_TFVARS_PATH does not exist: $PROD_TFVARS_PATH" >&2
  exit 2
fi

terraform_init() {
  terraform -chdir="$TERRAFORM_DIR" init -input=false -no-color \
    -backend-config=bucket=zenith-hub-tf-state-044528206149-us-east-1 \
    -backend-config=key=aws_baseline_80/terraform.tfstate \
    -backend-config=region="$AWS_REGION" \
    -backend-config=use_lockfile=true \
    -backend-config=encrypt=true
}

terraform_plan() {
  set +e
  terraform -chdir="$TERRAFORM_DIR" plan -no-color -input=false \
    -out="$TERRAFORM_PLAN_PATH" \
    -var-file="$PROD_TFVARS_PATH" \
    -var="image_tag=$IMAGE_TAG" \
    -var="gateway_image_tag=$GATEWAY_IMAGE_TAG" \
    -var="eventbus_image_tag=$EVENTBUS_IMAGE_TAG" \
    -var="cases_image_tag=$CASES_IMAGE_TAG" \
    -var="frank_image_tag=$FRANK_IMAGE_TAG" \
    -var="stt_image_tag=$STT_IMAGE_TAG" \
    | tee "$TERRAFORM_PLAN_TEXT"
  local plan_status=${PIPESTATUS[0]}
  set -e
  if [[ "$plan_status" -ne 0 ]]; then
    exit "$plan_status"
  fi
}

terraform_init
terraform fmt -check -diff "$TERRAFORM_DIR"
terraform -chdir="$TERRAFORM_DIR" validate -no-color
terraform_plan

if [[ "$ACTION" == "apply" ]]; then
  terraform -chdir="$TERRAFORM_DIR" apply -no-color -input=false "$TERRAFORM_PLAN_PATH"
fi
