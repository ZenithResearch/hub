#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:=us-west-2}"
: "${REPO_NAME:=agent-platform}"
: "${IMAGE_TAG:=latest}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
ECR_REPO_URL="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}"
IMAGE_URI="${ECR_REPO_URL}:${IMAGE_TAG}"

echo "ECR push"
echo "  region     : $AWS_REGION"
echo "  repo       : $REPO_NAME"
echo "  image tag  : $IMAGE_TAG"
echo "  image uri  : $IMAGE_URI"

aws ecr create-repository \
  --repository-name "$REPO_NAME" \
  --region "$AWS_REGION" >/dev/null 2>&1 || true

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com" >/dev/null

docker build -t "${REPO_NAME}:${IMAGE_TAG}" "$REPO_ROOT"
docker tag "${REPO_NAME}:${IMAGE_TAG}" "$IMAGE_URI"
docker push "$IMAGE_URI"

echo "$IMAGE_URI"

