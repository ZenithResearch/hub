#!/usr/bin/env bash
set -euo pipefail

# Build and push a Hub production container image without changing production.
# Deployment is intentionally separate and must go through Terraform via
# scripts/prod_terraform_cd.sh with explicit service image tags.
#
# Local operator example:
#   AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 \
#   IMAGE_TAG=runtime-config-main-$(git rev-parse --short=12 HEAD) \
#   scripts/prod_build_image.sh
#
# CI example after OIDC auth:
#   IMAGE_TAG=gateway-${GITHUB_SHA::12} scripts/prod_build_image.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${AWS_REGION:=us-east-1}"
: "${ECR_REPOSITORY:=zenith-hub-prod-gateway-http}"
: "${DOCKER_PLATFORM:=linux/amd64}"
: "${DOCKERFILE:=Dockerfile}"
: "${BUILD_CONTEXT:=.}"
: "${ALLOW_DIRTY:=0}"
: "${PUSH_IMAGE:=1}"
: "${PROVENANCE:=false}"

if [[ -z "${IMAGE_TAG:-}" ]]; then
  IMAGE_TAG="gateway-$(git rev-parse --short=12 HEAD)"
fi

if [[ ! "$IMAGE_TAG" =~ ^[A-Za-z0-9_.-]{1,128}$ ]]; then
  echo "Invalid Docker tag: $IMAGE_TAG" >&2
  exit 2
fi

if [[ "$ALLOW_DIRTY" != "1" ]]; then
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Refusing to build a production image from a dirty worktree. Set ALLOW_DIRTY=1 only for explicit emergency builds." >&2
    git status --short >&2
    exit 2
  fi
fi

AWS=(aws --region "$AWS_REGION")
if [[ -n "${AWS_PROFILE:-}" ]]; then
  AWS+=(--profile "$AWS_PROFILE")
fi

ACCOUNT_ID="$(${AWS[@]} sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"

"${AWS[@]}" ecr describe-repositories --repository-names "$ECR_REPOSITORY" >/dev/null
"${AWS[@]}" ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

BUILD_ARGS=(
  buildx build
  --platform "$DOCKER_PLATFORM"
  -f "$DOCKERFILE"
  -t "$IMAGE_URI"
  --provenance "$PROVENANCE"
)

if [[ "$PUSH_IMAGE" == "1" ]]; then
  BUILD_ARGS+=(--push)
else
  BUILD_ARGS+=(--load)
fi

if [[ -n "${DOCKER_CACHE_FROM:-}" ]]; then
  BUILD_ARGS+=(--cache-from "$DOCKER_CACHE_FROM")
fi
if [[ -n "${DOCKER_CACHE_TO:-}" ]]; then
  BUILD_ARGS+=(--cache-to "$DOCKER_CACHE_TO")
fi

BUILD_ARGS+=("$BUILD_CONTEXT")

docker "${BUILD_ARGS[@]}"

cat <<EOF
PUSHED_IMAGE_URI=$IMAGE_URI
IMAGE_TAG=$IMAGE_TAG
ECR_REPOSITORY=$ECR_REPOSITORY

Deployment is not performed by this script. To deploy, pass this tag explicitly to scripts/prod_terraform_cd.sh and preserve unaffected live service tags.
EOF
