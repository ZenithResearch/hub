#!/usr/bin/env bash
set -euo pipefail

# Narrow production deploy for the Gateway HTTP ECS service.
# This script intentionally changes only the Gateway task definition's app
# container image, then updates only the Gateway ECS service. It avoids broad
# Terraform applies for urgent Gateway-code rollout / freshness fixes.

: "${AWS_REGION:=us-east-1}"
: "${ECR_REPOSITORY:=zenith-hub-prod-gateway-http}"
: "${ECS_CLUSTER:=zenith-hub-prod-cluster}"
: "${ECS_SERVICE:=zenith-hub-prod-gateway-http}"
: "${CONTAINER_NAME:=app}"
: "${HUB_HEALTH_URL:=https://hub.zenith-research.ca/health}"
: "${IMAGE_TAG:?set IMAGE_TAG to the already-pushed gateway image tag}"

AWS=(aws --region "$AWS_REGION")
if [[ -n "${AWS_PROFILE:-}" ]]; then
  AWS+=(--profile "$AWS_PROFILE")
fi

ACCOUNT_ID="$(${AWS[@]} sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"

# Prove the requested image exists before touching ECS.
"${AWS[@]}" ecr describe-images \
  --repository-name "$ECR_REPOSITORY" \
  --image-ids imageTag="$IMAGE_TAG" \
  --query 'imageDetails[0].imageDigest' \
  --output text >/tmp/gateway-image-digest.txt
IMAGE_DIGEST="$(cat /tmp/gateway-image-digest.txt)"
if [[ -z "$IMAGE_DIGEST" || "$IMAGE_DIGEST" == "None" ]]; then
  echo "Gateway image tag not found in ECR: $IMAGE_TAG" >&2
  exit 2
fi

CURRENT_TASK_DEF="$(${AWS[@]} ecs describe-services \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE" \
  --query 'services[0].taskDefinition' \
  --output text)"

if [[ -z "$CURRENT_TASK_DEF" || "$CURRENT_TASK_DEF" == "None" ]]; then
  echo "Could not resolve current task definition for $ECS_SERVICE" >&2
  exit 2
fi

TMPDIR="${RUNNER_TEMP:-/tmp}"
CURRENT_JSON="$TMPDIR/gateway-current-task-definition.json"
NEXT_JSON="$TMPDIR/gateway-next-task-definition.json"

"${AWS[@]}" ecs describe-task-definition \
  --task-definition "$CURRENT_TASK_DEF" \
  --query taskDefinition \
  --output json > "$CURRENT_JSON"

jq --arg image "$IMAGE_URI" --arg container "$CONTAINER_NAME" '
  {
    family,
    taskRoleArn,
    executionRoleArn,
    networkMode,
    containerDefinitions,
    volumes,
    placementConstraints,
    requiresCompatibilities,
    cpu,
    memory,
    ephemeralStorage,
    runtimePlatform,
    ipcMode,
    pidMode,
    proxyConfiguration,
    inferenceAccelerators
  }
  | with_entries(select(.value != null))
  | .containerDefinitions |= map(
      if .name == $container then .image = $image else . end
    )
' "$CURRENT_JSON" > "$NEXT_JSON"

if ! jq -e --arg image "$IMAGE_URI" --arg container "$CONTAINER_NAME" '
  .containerDefinitions[] | select(.name == $container and .image == $image)
' "$NEXT_JSON" >/dev/null; then
  echo "Failed to set $CONTAINER_NAME image to $IMAGE_URI" >&2
  exit 2
fi

NEW_TASK_DEF="$(${AWS[@]} ecs register-task-definition \
  --cli-input-json "file://$NEXT_JSON" \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)"

"${AWS[@]}" ecs update-service \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --task-definition "$NEW_TASK_DEF" \
  --query 'service.serviceArn' \
  --output text >/tmp/gateway-updated-service-arn.txt

"${AWS[@]}" ecs wait services-stable \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE"

SERVICE_STATE=""
for _ in $(seq 1 40); do
  SERVICE_STATE="$(${AWS[@]} ecs describe-services \
    --cluster "$ECS_CLUSTER" \
    --services "$ECS_SERVICE" \
    --query 'services[0].{serviceName:serviceName,desired:desiredCount,running:runningCount,pending:pendingCount,taskDefinition:taskDefinition,rolloutState:deployments[?status==`PRIMARY`]|[0].rolloutState}' \
    --output json)"
  PRIMARY_TASK_DEF="$(jq -r '.taskDefinition // ""' <<<"$SERVICE_STATE")"
  PRIMARY_ROLLOUT_STATE="$(jq -r '.rolloutState // ""' <<<"$SERVICE_STATE")"
  if [[ "$PRIMARY_TASK_DEF" == "$NEW_TASK_DEF" && "$PRIMARY_ROLLOUT_STATE" == "COMPLETED" ]]; then
    break
  fi
  sleep 10
done

if [[ "$PRIMARY_TASK_DEF" != "$NEW_TASK_DEF" || "$PRIMARY_ROLLOUT_STATE" != "COMPLETED" ]]; then
  echo "Primary deployment did not settle on the newly registered task definition" >&2
  echo "$SERVICE_STATE" >&2
  exit 2
fi

HEALTH_BODY="$TMPDIR/gateway-health.json"
HEALTH_STATUS="$(curl -fsS -o "$HEALTH_BODY" -w '%{http_code}' "$HUB_HEALTH_URL" || true)"
if [[ "$HEALTH_STATUS" != "200" ]]; then
  echo "Gateway health check failed: HTTP $HEALTH_STATUS" >&2
  if [[ -s "$HEALTH_BODY" ]]; then
    head -c 500 "$HEALTH_BODY" >&2 || true
    echo >&2
  fi
  exit 2
fi

cat <<EOF
GATEWAY_DEPLOY_OK=1
IMAGE_URI=$IMAGE_URI
IMAGE_TAG=$IMAGE_TAG
IMAGE_DIGEST=$IMAGE_DIGEST
ECS_CLUSTER=$ECS_CLUSTER
ECS_SERVICE=$ECS_SERVICE
PREVIOUS_TASK_DEFINITION=$CURRENT_TASK_DEF
NEW_TASK_DEFINITION=$NEW_TASK_DEF
SERVICE_STATE=$SERVICE_STATE
HEALTH_URL=$HUB_HEALTH_URL
HEALTH_STATUS=$HEALTH_STATUS
EOF
