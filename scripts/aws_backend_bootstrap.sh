#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:=us-west-2}"
: "${STATE_BUCKET:?Set STATE_BUCKET (S3 bucket for Terraform state)}"
: "${LOCK_TABLE:?Set LOCK_TABLE (DynamoDB table for Terraform state locking)}"

echo "Bootstrapping Terraform backend in region: $AWS_REGION"
echo "  bucket: $STATE_BUCKET"
echo "  table : $LOCK_TABLE"

bucket_exists() {
  aws s3api head-bucket --bucket "$STATE_BUCKET" --region "$AWS_REGION" >/dev/null 2>&1
}

table_exists() {
  aws dynamodb describe-table --table-name "$LOCK_TABLE" --region "$AWS_REGION" >/dev/null 2>&1
}

if bucket_exists; then
  echo "S3 bucket already exists: $STATE_BUCKET"
else
  echo "Creating S3 bucket: $STATE_BUCKET"
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$AWS_REGION" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "$STATE_BUCKET" \
      --region "$AWS_REGION" \
      --create-bucket-configuration "LocationConstraint=$AWS_REGION" >/dev/null
  fi
fi

echo "Enabling bucket versioning"
aws s3api put-bucket-versioning \
  --bucket "$STATE_BUCKET" \
  --versioning-configuration Status=Enabled \
  --region "$AWS_REGION" >/dev/null

echo "Blocking public access (recommended)"
aws s3api put-public-access-block \
  --bucket "$STATE_BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
  --region "$AWS_REGION" >/dev/null

echo "Enabling default encryption (recommended)"
aws s3api put-bucket-encryption \
  --bucket "$STATE_BUCKET" \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
  --region "$AWS_REGION" >/dev/null

if table_exists; then
  echo "DynamoDB table already exists: $LOCK_TABLE"
else
  echo "Creating DynamoDB table: $LOCK_TABLE"
  aws dynamodb create-table \
    --table-name "$LOCK_TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$AWS_REGION" >/dev/null
fi

echo "Backend bootstrap complete."

