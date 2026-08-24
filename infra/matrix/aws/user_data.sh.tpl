#!/bin/bash
set -euo pipefail
umask 077

exec 3>&1 4>&2
set +x

DATA_MOUNT=/opt/matrix-data
MATRIX_DIR=/opt/matrix
SECRET_JSON=$(mktemp /run/matrix-secret.XXXXXX)
BROKER_BOOTSTRAP_ENV=$(mktemp /run/hypha-admin-broker-bootstrap.XXXXXX)
BROKER_DOCKER_CONFIG=$(mktemp -d /run/hypha-admin-broker-docker.XXXXXX)
export DOCKER_CONFIG="$BROKER_DOCKER_CONFIG"
trap 'rm -f "$SECRET_JSON" "$BROKER_BOOTSTRAP_ENV"; rm -rf "$BROKER_DOCKER_CONFIG"' EXIT

dnf install -y awscli docker jq xfsprogs

# Resolve only the tagged data volume attached to this instance. Nitro names
# EBS devices by volume ID, so never trust the requested /dev/sdX alias.
IMDS_TOKEN=$(curl -fsS --max-time 5 -X PUT \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 300' \
  http://169.254.169.254/latest/api/token)
INSTANCE_ID=$(curl -fsS --max-time 5 \
  -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-id)
mapfile -t VOLUME_IDS < <(aws ec2 describe-volumes \
  --region '${aws_region}' \
  --filters \
    "Name=attachment.instance-id,Values=$INSTANCE_ID" \
    'Name=tag:Name,Values=hypha-fresh-synapse-data' \
  --query 'Volumes[].VolumeId' \
  --output text | tr '\t' '\n')
[ "$${#VOLUME_IDS[@]}" -eq 1 ] || { echo "Expected exactly one tagged EBS data volume" >&2; exit 1; }
EXPECTED_VOLUME_ID="$${VOLUME_IDS[0]}"
EXPECTED_BY_ID="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_$${EXPECTED_VOLUME_ID//-/}"
export EXPECTED_BY_ID
timeout 120 bash -c 'until [ -e "$EXPECTED_BY_ID" ]; do sleep 2; done'
DATA_DEVICE=$(readlink -f "$EXPECTED_BY_ID")
[ -b "$DATA_DEVICE" ] || { echo "Tagged EBS data device not found" >&2; exit 1; }
FILESYSTEM_TYPE=$(blkid -s TYPE -o value "$DATA_DEVICE" 2>/dev/null || true)
FILESYSTEM_LABEL=$(blkid -s LABEL -o value "$DATA_DEVICE" 2>/dev/null || true)
if [ -z "$FILESYSTEM_TYPE" ]; then
  mkfs.xfs -L hypha-matrix "$DATA_DEVICE" >/dev/null
  FILESYSTEM_TYPE="xfs"
  FILESYSTEM_LABEL="hypha-matrix"
fi
[ "$FILESYSTEM_TYPE" = "xfs" ] && [ "$FILESYSTEM_LABEL" = "hypha-matrix" ] || {
  echo "Refusing unexpected filesystem on tagged EBS data volume" >&2
  exit 1
}
DATA_UUID=$(blkid -s UUID -o value "$DATA_DEVICE")
mkdir -p "$DATA_MOUNT"
grep -Fq "UUID=$DATA_UUID " /etc/fstab || \
  echo "UUID=$DATA_UUID $DATA_MOUNT xfs defaults,nodev,nosuid 0 2" >> /etc/fstab
mountpoint -q "$DATA_MOUNT" || mount "$DATA_MOUNT"
mountpoint -q "$DATA_MOUNT" || { echo "Matrix data volume is not mounted" >&2; exit 1; }
findmnt --verify --verbose >/dev/null

mkdir -p "$MATRIX_DIR" "$DATA_MOUNT/postgres" "$DATA_MOUNT/synapse" "$DATA_MOUNT/caddy-data" "$DATA_MOUNT/caddy-config"
chmod 700 "$MATRIX_DIR" "$DATA_MOUNT"
chown 991:991 "$DATA_MOUNT/synapse"

install -d -m 0755 /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/matrix-data.conf <<'EOF_DOCKER_MOUNT'
[Unit]
RequiresMountsFor=/opt/matrix-data
EOF_DOCKER_MOUNT
systemctl daemon-reload
systemctl enable --now docker

COMPOSE_VERSION=2.27.0
COMPOSE_SHA256=f3ba3bf1e4ab18e96c2d36526a075a02a78fb5f8e80d3e3ca9c5bf256d81d0a0
install -d -m 0755 /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/v$COMPOSE_VERSION/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
printf '%s  %s\n' "$COMPOSE_SHA256" /usr/local/lib/docker/cli-plugins/docker-compose | sha256sum -c -
chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose

# Fetch AWSCURRENT only at runtime. Neither SecretString nor derived values are
# printed. A bounded wait gates all containers until the secret is populated.
SECRET_READY=false
for _attempt in $(seq 1 30); do
  if aws secretsmanager get-secret-value \
    --region '${aws_region}' \
    --secret-id '${secret_arn}' \
    --version-stage AWSCURRENT \
    --query SecretString \
    --output text > "$SECRET_JSON" 2>/dev/null; then
    SECRET_READY=true
    break
  fi
  sleep 10
done
[ "$SECRET_READY" = true ] || { echo "AWSCURRENT Matrix secret version unavailable" >&2; exit 1; }
chmod 600 "$SECRET_JSON"

python3 - "$SECRET_JSON" "$MATRIX_DIR" "$BROKER_BOOTSTRAP_ENV" <<'PY'
import json
import os
import re
import sys

secret_path, matrix_dir, bootstrap_env_path = sys.argv[1:]
required = {
    "POSTGRES_PASSWORD",
    "REGISTRATION_SHARED_SECRET",
    "MACAROON_SECRET_KEY",
    "FORM_SECRET",
    "HYPHA_ADMIN_BROKER_SECRET_VERIFIER",
    "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD",
}
with open(secret_path, encoding="utf-8") as handle:
    values = json.load(handle)
if not isinstance(values, dict) or set(values) != required:
    raise SystemExit("Secret JSON must contain exactly the documented keys")
ordinary_keys = required - {"HYPHA_ADMIN_BROKER_SECRET_VERIFIER"}
if any(not isinstance(values[key], str) or not re.fullmatch(r"[A-Za-z0-9._~!@#%^*+=:-]{32,512}", values[key]) for key in ordinary_keys):
    raise SystemExit("Every secret value must satisfy the documented bounded format")
if not isinstance(values["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"], str) or not re.fullmatch(
    r"scrypt\$[0-9]+\$[0-9]+\$[0-9]+\$[A-Za-z0-9_-]+\$[A-Za-z0-9_-]+",
    values["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"],
):
    raise SystemExit("The administration verifier must use the documented scrypt format")

def quoted(value):
    return json.dumps(value)

env_path = os.path.join(matrix_dir, ".env")
with open(env_path, "w", encoding="utf-8") as handle:
    handle.write("POSTGRES_PASSWORD=" + values["POSTGRES_PASSWORD"] + "\n")
os.chmod(env_path, 0o600)

broker_env_path = os.path.join(matrix_dir, "broker.env")
with open(broker_env_path, "w", encoding="utf-8") as handle:
    handle.write("HYPHA_ADMIN_BROKER_SECRET_VERIFIER='" + values["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"] + "'\n")
    handle.write("HYPHA_ADMIN_BROKER_SERVICE_PASSWORD='" + values["HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"] + "'\n")
os.chmod(broker_env_path, 0o600)

with open(bootstrap_env_path, "w", encoding="utf-8") as handle:
    handle.write("REGISTRATION_SHARED_SECRET='" + values["REGISTRATION_SHARED_SECRET"] + "'\n")
    handle.write("HYPHA_ADMIN_BROKER_SERVICE_PASSWORD='" + values["HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"] + "'\n")
    handle.write("MATRIX_SERVER_NAME='" + ${matrix_server_name_json} + "'\n")
os.chmod(bootstrap_env_path, 0o600)

config_path = os.path.join(matrix_dir, "homeserver.yaml")
with open(config_path, "w", encoding="utf-8") as handle:
    handle.write(f'''server_name: ${matrix_server_name_json}
pid_file: /data/homeserver.pid
public_baseurl: ${matrix_public_url_json}
serve_server_wellknown: true
listeners:
  - port: 8008
    tls: false
    type: http
    x_forwarded: true
    resources:
      - names: [client, federation]
database:
  name: psycopg2
  args:
    user: synapse
    password: {quoted(values["POSTGRES_PASSWORD"])}
    database: synapse
    host: matrix-db
    port: 5432
log_config: /config/log.config
media_store_path: /data/media_store
signing_key_path: /data/server.signing.key
registration_shared_secret: {quoted(values["REGISTRATION_SHARED_SECRET"])}
macaroon_secret_key: {quoted(values["MACAROON_SECRET_KEY"])}
form_secret: {quoted(values["FORM_SECRET"])}
password_config:
  enabled: true
enable_registration: false
report_stats: false
trusted_key_servers:
  - server_name: matrix.org
''')
os.chown(config_path, 0, 991)
os.chmod(config_path, 0o640)
PY
chmod 600 /opt/matrix/.env
chmod 600 /opt/matrix/broker.env

cat > "$MATRIX_DIR/log.config" <<'EOF_LOG'
version: 1
formatters:
  normal:
    format: '%(asctime)s %(name)s %(levelname)s %(message)s'
handlers:
  console:
    class: logging.StreamHandler
    formatter: normal
root:
  level: WARNING
  handlers: [console]
disable_existing_loggers: false
EOF_LOG
chown root:991 "$MATRIX_DIR/log.config"
chmod 640 "$MATRIX_DIR/log.config"

cat > "$MATRIX_DIR/Caddyfile" <<'EOF_CADDY'
${matrix_server_name} {
  encode zstd gzip
  handle /_hypha/admin/v1/* {
    request_body {
      max_size 64KB
    }
    reverse_proxy hypha-admin-broker:8080
  }
  handle {
    reverse_proxy matrix-synapse:8008
  }
}
EOF_CADDY

cat > "$MATRIX_DIR/compose.yaml" <<'EOF_COMPOSE'
services:
  matrix-db:
    image: ${postgres_image}
    container_name: matrix-db
    restart: unless-stopped
    env_file: .env
    environment:
      POSTGRES_USER: synapse
      POSTGRES_DB: synapse
      POSTGRES_PASSWORD: "$${POSTGRES_PASSWORD}"
      POSTGRES_INITDB_ARGS: "--encoding=UTF8 --locale=C"
    volumes:
      - /opt/matrix-data/postgres:/var/lib/postgresql/data
    networks: [matrix-internal]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U synapse -d synapse"]
      interval: 10s
      timeout: 5s
      retries: 12

  matrix-synapse:
    image: ${synapse_image}
    container_name: matrix-synapse
    restart: unless-stopped
    depends_on:
      matrix-db:
        condition: service_healthy
    environment:
      SYNAPSE_CONFIG_PATH: /config/homeserver.yaml
    volumes:
      - /opt/matrix-data/synapse:/data
      - /opt/matrix/homeserver.yaml:/config/homeserver.yaml:ro
      - /opt/matrix/log.config:/config/log.config:ro
    networks: [matrix-internal]

  # BEGIN HYPHA ADMIN BROKER
  hypha-admin-broker:
    image: ${admin_broker_image}
    container_name: hypha-admin-broker
    restart: unless-stopped
    depends_on: [matrix-synapse]
    user: "65532:65532"
    read_only: true
    env_file: /opt/matrix/broker.env
    environment:
      HYPHA_ADMIN_BROKER_SERVICE_USER_ID: '@_hypha_admin_broker:${matrix_server_name}'
    tmpfs:
      - /tmp:noexec,nosuid,size=16m
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    networks: [matrix-internal]
  # END HYPHA ADMIN BROKER

  caddy:
    image: ${caddy_image}
    container_name: matrix-caddy
    restart: unless-stopped
    depends_on: [matrix-synapse]
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /opt/matrix/Caddyfile:/etc/caddy/Caddyfile:ro
      - /opt/matrix-data/caddy-data:/data
      - /opt/matrix-data/caddy-config:/config
    networks: [edge, matrix-internal]

networks:
  edge:
  matrix-internal:
    internal: true
EOF_COMPOSE

# Generate only the fresh signing material before using the hardened config.
docker run --rm \
  -v "$DATA_MOUNT/synapse:/data" \
  -e SYNAPSE_SERVER_NAME='${matrix_server_name}' \
  -e SYNAPSE_REPORT_STATS=no \
  '${synapse_image}' generate >/dev/null
rm -f "$DATA_MOUNT/synapse/homeserver.yaml" "$DATA_MOUNT/synapse"/*.log.config

docker compose --project-directory "$MATRIX_DIR" -f "$MATRIX_DIR/compose.yaml" config --quiet
docker compose --project-directory "$MATRIX_DIR" -f "$MATRIX_DIR/compose.yaml" up -d matrix-db matrix-synapse
for _attempt in $(seq 1 120); do
  if docker exec matrix-synapse python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8008/_matrix/client/versions', timeout=5); assert r.status == 200" >/dev/null 2>&1; then
    break
  fi
  [ "$_attempt" -lt 120 ] || { echo "Synapse did not become ready for broker authority bootstrap" >&2; exit 1; }
  sleep 5
done
aws ecr get-login-password --region '${aws_region}' |
  docker login --username AWS --password-stdin \
    '610992396917.dkr.ecr.us-east-1.amazonaws.com' >/dev/null
docker pull '${admin_broker_image}' >/dev/null
docker run --rm \
  --network matrix_matrix-internal \
  --env-file "$BROKER_BOOTSTRAP_ENV" \
  --entrypoint python \
  '${admin_broker_image}' \
  /app/scripts/bootstrap_hypha_admin_broker_authority.py >/dev/null
rm -f "$BROKER_BOOTSTRAP_ENV"
docker compose --project-directory "$MATRIX_DIR" -f "$MATRIX_DIR/compose.yaml" up -d
for _attempt in $(seq 1 60); do
  if docker exec hypha-admin-broker python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/_hypha/admin/v1/ready', timeout=15); assert r.status == 200" >/dev/null 2>&1; then
    break
  fi
  [ "$_attempt" -lt 60 ] || { echo "Hypha administration broker did not become ready" >&2; exit 1; }
  sleep 5
done
