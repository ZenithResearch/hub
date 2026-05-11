#!/usr/bin/env bash
set -euo pipefail

need() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

need docker
need aws
need terraform
need python3

if ! docker compose version >/dev/null 2>&1; then
  echo "Missing required command: docker compose (v2)" >&2
  exit 1
fi

echo "OK: docker"
docker --version | sed 's/^/  /'
echo "OK: docker compose"
docker compose version | sed 's/^/  /'
echo "OK: aws"
aws --version 2>&1 | sed 's/^/  /'
echo "OK: terraform"
terraform version | head -n 1 | sed 's/^/  /'
echo "OK: python3"
python3 --version | sed 's/^/  /'

if aws sts get-caller-identity >/dev/null 2>&1; then
  echo "OK: AWS credentials"
else
  echo "WARN: AWS credentials not configured (aws sts get-caller-identity failed)" >&2
fi

