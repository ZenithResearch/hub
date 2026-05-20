#!/usr/bin/env bash
set -euo pipefail

# Local/CI baseline checks for Hub. This intentionally avoids production deploys,
# Terraform plans against the remote backend, and any command that requires AWS credentials.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
TERRAFORM_BIN="${TERRAFORM_BIN:-terraform}"

if [[ "${SKIP_PYTHON_TESTS:-0}" != "1" ]]; then
  "$PYTHON_BIN" -m pytest tests -q
fi

if [[ "${SKIP_PRIVATE_ARTIFACT_SCAN:-0}" != "1" ]]; then
  if [[ -n "${PRIVATE_ARTIFACT_SCAN_RANGE:-}" ]]; then
    "$PYTHON_BIN" scripts/private_artifact_scan.py --range "$PRIVATE_ARTIFACT_SCAN_RANGE"
  elif git rev-parse --verify origin/main >/dev/null 2>&1; then
    "$PYTHON_BIN" scripts/private_artifact_scan.py --range origin/main...HEAD
  else
    "$PYTHON_BIN" scripts/private_artifact_scan.py
  fi
fi

if [[ "${SKIP_STATIC_CONTRACT_CHECKS:-0}" != "1" ]]; then
  "$PYTHON_BIN" scripts/model_profile_check.py >/dev/null
  "$PYTHON_BIN" scripts/matrix_deployment_check.py >/dev/null
  "$PYTHON_BIN" scripts/deployment_profile_check.py >/dev/null
  if [[ -n "${HUB_REMOTE_ROOT:-}" || -d "/Volumes/BJJ-Cache/zenith-cache" || -d "/Volumes/BJJ/zenith-cache" ]]; then
    "$PYTHON_BIN" scripts/external_root_check.py >/dev/null
  fi
fi

if [[ "${SKIP_TERRAFORM:-0}" != "1" ]]; then
  "$TERRAFORM_BIN" -chdir=infra/aws_baseline_80 fmt -check -diff

  # Use a disposable TF_DATA_DIR when available so CI/local validation does not
  # reuse a production backend initialization. -backend=false keeps this check
  # credentials-free and prevents accidental remote state access.
  export TF_DATA_DIR="${TF_DATA_DIR:-${RUNNER_TEMP:-/tmp}/hub-terraform-ci-${GITHUB_RUN_ID:-local}-$$}"
  "$TERRAFORM_BIN" -chdir=infra/aws_baseline_80 init -backend=false -input=false -no-color
  "$TERRAFORM_BIN" -chdir=infra/aws_baseline_80 validate -no-color
fi

if [[ "${SKIP_COMPOSE_CONFIG:-0}" != "1" ]] && command -v docker >/dev/null 2>&1; then
  docker compose config --quiet
fi
