#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:=us-west-2}"
: "${QDRANT_SECRET_NAME:=agent-platform/qdrant_api_key}"
: "${QDRANT_API_KEY:?Set QDRANT_API_KEY (will be stored in Secrets Manager)}"

echo "Ensuring secret exists and setting value"
echo "  region: $AWS_REGION"
echo "  name  : $QDRANT_SECRET_NAME"

if aws secretsmanager describe-secret --secret-id "$QDRANT_SECRET_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value \
    --secret-id "$QDRANT_SECRET_NAME" \
    --secret-string "$QDRANT_API_KEY" \
    --region "$AWS_REGION" >/dev/null
else
  aws secretsmanager create-secret \
    --name "$QDRANT_SECRET_NAME" \
    --secret-string "$QDRANT_API_KEY" \
    --region "$AWS_REGION" >/dev/null
fi

aws secretsmanager describe-secret \
  --secret-id "$QDRANT_SECRET_NAME" \
  --query ARN \
  --output text \
  --region "$AWS_REGION"

