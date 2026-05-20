#!/usr/bin/env bash
set -euo pipefail

# Run Terraform for a module using Hub's configurable external root instead of
# repo-local .terraform directories or the system temp directory.
#
# Usage:
#   scripts/terraform_external.sh infra/aws_baseline_80 validate -no-color
#   HUB_REMOTE_ROOT=/Volumes/BJJ-Cache/zenith-cache scripts/terraform_external.sh infra/matrix/aws init -backend=false

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <terraform-module-dir> <terraform args...>" >&2
  exit 2
fi

MODULE_DIR="$1"
shift

if [ ! -d "$MODULE_DIR" ]; then
  echo "terraform module dir not found: $MODULE_DIR" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
if [ -n "${HUB_REMOTE_ROOT:-}" ]; then
  source "$SCRIPT_DIR/hub_external_env.sh" "$HUB_REMOTE_ROOT"
else
  source "$SCRIPT_DIR/hub_external_env.sh"
fi

safe_module="$(printf '%s' "$MODULE_DIR" | tr '/ .' '___' | tr -cd 'A-Za-z0-9_-')"
export TF_DATA_DIR="$HUB_TERRAFORM_DATA_ROOT/$safe_module"
mkdir -p "$TF_DATA_DIR"

exec terraform -chdir="$MODULE_DIR" "$@"
