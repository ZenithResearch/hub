#!/usr/bin/env bash
# Source this file to configure Hub local tooling to use an external root.
# Usage:
#   source scripts/hub_external_env.sh /Volumes/BJJ-Cache/zenith-cache
#   source scripts/hub_external_env.sh   # uses HUB_REMOTE_ROOT or first mounted default

hub_external_find_root() {
  if [ -n "${HUB_REMOTE_ROOT:-}" ] && [ -d "$HUB_REMOTE_ROOT" ]; then
    printf '%s\n' "$HUB_REMOTE_ROOT"
    return 0
  fi
  for candidate in /Volumes/BJJ-Cache/zenith-cache /Volumes/BJJ/zenith-cache /mnt/zenith-cache /media/zenith-cache; do
    if [ -d "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if [ -n "${1:-}" ]; then
  export HUB_REMOTE_ROOT="$1"
else
  if root="$(hub_external_find_root)"; then
    export HUB_REMOTE_ROOT="$root"
  else
    echo "hub_external_env: no external root found; pass one explicitly" >&2
    return 2 2>/dev/null || exit 2
  fi
fi

mkdir -p \
  "$HUB_REMOTE_ROOT/tooling-cache/terraform-plugin-cache" \
  "$HUB_REMOTE_ROOT/tooling-state/terraform" \
  "$HUB_REMOTE_ROOT/temp" \
  "$HUB_REMOTE_ROOT/model-artifacts" \
  "$HUB_REMOTE_ROOT/build-cache" \
  "$HUB_REMOTE_ROOT/runtime-state"

export TF_PLUGIN_CACHE_DIR="$HUB_REMOTE_ROOT/tooling-cache/terraform-plugin-cache"
export HUB_EXTERNAL_TMPDIR="$HUB_REMOTE_ROOT/temp"
export HUB_TERRAFORM_DATA_ROOT="$HUB_REMOTE_ROOT/tooling-state/terraform"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HUB_REMOTE_ROOT/tooling-cache/xdg}"
export HF_HOME="${HF_HOME:-$HUB_REMOTE_ROOT/model-artifacts/huggingface}"

# Terraform provider plugins need Unix domain sockets. exFAT/NTFS volumes often
# do not support those, so only move TMPDIR to the external root when the mount
# supports socket creation. Keep HUB_EXTERNAL_TMPDIR available for tools that
# only need regular temp files.
if python3 - "$HUB_EXTERNAL_TMPDIR" >/dev/null 2>&1 <<'PY'
import os, socket, sys
path = os.path.join(sys.argv[1], '.hub-socket-probe.sock')
try:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(path)
    sock.close()
    os.unlink(path)
except OSError:
    raise SystemExit(1)
PY
then
  export TMPDIR="$HUB_EXTERNAL_TMPDIR"
else
  export HUB_EXTERNAL_TMPDIR_SOCKET_UNSUPPORTED=1
fi

printf 'HUB_REMOTE_ROOT=%s\n' "$HUB_REMOTE_ROOT"
printf 'TF_PLUGIN_CACHE_DIR=%s\n' "$TF_PLUGIN_CACHE_DIR"
printf 'HUB_TERRAFORM_DATA_ROOT=%s\n' "$HUB_TERRAFORM_DATA_ROOT"
printf 'HUB_EXTERNAL_TMPDIR=%s\n' "$HUB_EXTERNAL_TMPDIR"
printf 'TMPDIR=%s\n' "${TMPDIR:-}"
