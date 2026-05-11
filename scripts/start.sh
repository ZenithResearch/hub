#!/bin/bash
# start.sh — Start the hub backend locally in one shot.
#
# First run: copies .env.example if .env is missing, runs Matrix bot setup
#            if tokens haven't been generated yet, then starts everything.
#
# Subsequent runs: just starts services that aren't already running.
#
# Usage: ./scripts/start.sh
#        make start

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB_DIR="$SCRIPT_DIR/.."
ENV_FILE="$HUB_DIR/.env"

# ── .env ─────────────────────────────────────────────────────────────────────

if [ ! -f "$ENV_FILE" ]; then
  echo "→ No .env found — copying from .env.example..."
  cp "$HUB_DIR/.env.example" "$ENV_FILE"
  echo "  Edit $ENV_FILE with your API keys, then re-run this script."
  exit 1
fi

source "$ENV_FILE"

# ── First-time Matrix bot setup ───────────────────────────────────────────────

if [ -z "$GATEWAY_BOT_AS_TOKEN" ] || [ -z "$BRIDGE_BOT_HS_TOKEN" ]; then
  echo "→ Matrix appservice tokens not found — running first-time bot setup..."
  echo "  (This starts Matrix, generates tokens, registers appservices, creates the feedback room)"
  echo ""

  # Start Matrix first so the room can be created at the end of setup
  echo "→ Starting Matrix..."
  docker compose -f "$HUB_DIR/infra/matrix/docker-compose.yml" \
    --env-file "$ENV_FILE" up -d

  echo "→ Waiting for Matrix to be healthy..."
  for i in $(seq 1 30); do
    if curl -sf "http://localhost:${MATRIX_HTTP_PORT:-8008}/health" > /dev/null 2>&1; then
      echo "  Matrix healthy"
      break
    fi
    sleep 2
  done

  bash "$SCRIPT_DIR/setup_matrix_bots.sh"

  # Reload .env now that tokens have been appended
  source "$ENV_FILE"
else
  # Matrix appservice registrations are rendered inside the Synapse container
  # into /data/appservices from the current .env. Do not write generated YAMLs
  # into infra/matrix/appservices or mutate the tracked homeserver template.

  # ── Start Matrix ─────────────────────────────────────────────────────────────
  echo "→ Starting Matrix..."
  docker compose -f "$HUB_DIR/infra/matrix/docker-compose.yml" \
    --env-file "$ENV_FILE" up -d

  echo "→ Waiting for Matrix to be healthy..."
  for i in $(seq 1 30); do
    if curl -sf "http://localhost:${MATRIX_HTTP_PORT:-8008}/health" > /dev/null 2>&1; then
      echo "  Matrix healthy"
      break
    fi
    sleep 2
  done
fi

# ── Build Hermes worker sandbox image ────────────────────────────────────────

echo "→ Building Hermes worker sandbox image (hub-worker)..."
docker compose -f "$HUB_DIR/docker-compose.yml" \
  --env-file "$ENV_FILE" \
  --profile build-only build hermes-worker

# ── Start hub services ────────────────────────────────────────────────────────

echo "→ Starting hub services..."
HUB_SERVICES=(gateway-http frank matrix-bridge hermes-worker-queue)
if [ "${MATRIX_REGISTER_SOPHIA_APP_SERVICE:-false}" = "true" ] || [ "${MATRIX_REGISTER_SOPHIA_APP_SERVICE:-false}" = "1" ]; then
  HUB_SERVICES+=(ingest)
fi

docker compose -f "$HUB_DIR/docker-compose.yml" \
  --env-file "$ENV_FILE" \
  up -d "${HUB_SERVICES[@]}"

echo ""
echo "✓ Hub is up."
echo ""
echo "  Matrix          http://localhost:${MATRIX_HTTP_PORT:-8008}"
echo "  Gateway HTTP    http://localhost:${HTTP_PORT:-8080}"
echo "  Frank           watching queue.job.enqueued"
echo "  Matrix bridge   watching feedback room → queue"
echo "  Hermes worker   shared worker queue consumer running"
echo ""
echo "  Logs:"
echo "    docker logs -f hub-frank-1"
echo "    docker logs -f hub-matrix-bridge-1"
echo "    docker logs -f hub-hermes-worker-queue-1"
