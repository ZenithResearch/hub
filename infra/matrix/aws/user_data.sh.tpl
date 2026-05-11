#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────
# Hub Matrix bootstrap — Amazon Linux 2023
# ──────────────────────────────────────────────

# Mount and format the data EBS volume
DATA_DEVICE="/dev/xvdf"
DATA_MOUNT="/opt/matrix-data"

if ! blkid "$DATA_DEVICE"; then
  mkfs.xfs "$DATA_DEVICE"
fi

mkdir -p "$DATA_MOUNT"
mount "$DATA_DEVICE" "$DATA_MOUNT"
echo "$DATA_DEVICE $DATA_MOUNT xfs defaults,nofail 0 2" >> /etc/fstab

# Subdirs for Synapse and Postgres
mkdir -p "$DATA_MOUNT/synapse" "$DATA_MOUNT/postgres"

# Install Docker
dnf install -y docker
systemctl enable --now docker

# Install Docker Compose plugin
COMPOSE_VERSION="2.27.0"
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/v$${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Write environment file (secrets injected by Terraform templatefile)
cat > /opt/matrix.env << 'ENVEOF'
MATRIX_SERVER_NAME=${matrix_server_name}
MATRIX_DB_PASSWORD=${matrix_db_password}
MATRIX_DB_USER=synapse
MATRIX_DB_NAME=synapse
MATRIX_DB_HOST=matrix-db
MATRIX_DB_PORT=5432
MATRIX_REGISTRATION_SECRET=${matrix_registration_secret}
MATRIX_MACAROON_SECRET=${matrix_macaroon_secret}
MATRIX_FORM_SECRET=${matrix_form_secret}
MATRIX_FEDERATION_ENABLED=${matrix_federation_enabled}
MATRIX_ENABLE_REGISTRATION=${matrix_enable_registration}
ENVEOF
chmod 600 /opt/matrix.env

# Write Synapse homeserver config (env vars substituted at runtime by Synapse's SYNAPSE_CONFIG_PATH handling)
mkdir -p /opt/matrix-config
cat > /opt/matrix-config/homeserver.yaml << 'HOMEEOF'
server_name: "${matrix_server_name}"
pid_file: /data/homeserver.pid

listeners:
  - port: 8008
    tls: false
    type: http
    x_forwarded: true
    resources:
      - names: [client, federation]
        compress: false
  - port: 8448
    tls: false
    type: http
    x_forwarded: true
    resources:
      - names: [federation]
        compress: false

database:
  name: psycopg2
  args:
    user: "synapse"
    password: "${matrix_db_password}"
    database: "synapse"
    host: "matrix-db"
    port: "5432"
    cp_min: 5
    cp_max: 10

log_config: "/data/log.config"
media_store_path: "/data/media_store"
registration_shared_secret: "${matrix_registration_secret}"
report_stats: false
macaroon_secret_key: "${matrix_macaroon_secret}"
form_secret: "${matrix_form_secret}"
signing_key_path: "/data/${matrix_server_name}.signing.key"

trusted_key_servers:
  - server_name: "matrix.org"

federation_enabled: ${matrix_federation_enabled}
enable_registration: ${matrix_enable_registration}
HOMEEOF

cat > /opt/matrix-config/log.config << 'LOGEOF'
version: 1
formatters:
  precise:
    format: '%(asctime)s - %(name)s - %(lineno)d - %(levelname)s - %(request)s - %(message)s'
handlers:
  console:
    class: logging.StreamHandler
    formatter: precise
loggers:
  synapse.storage.SQL:
    level: WARNING
root:
  level: WARNING
  handlers: [console]
disable_existing_loggers: false
LOGEOF

# Write Docker Compose file
cat > /opt/docker-compose.matrix.yml << 'COMPOSEEOF'
version: "3.8"

services:
  matrix-db:
    image: postgres:16-alpine
    container_name: matrix-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: synapse
      POSTGRES_PASSWORD: ${matrix_db_password}
      POSTGRES_DB: synapse
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - /opt/matrix-data/postgres:/var/lib/postgresql/data
    networks:
      - matrix-net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U synapse"]
      interval: 10s
      timeout: 5s
      retries: 5

  matrix-synapse:
    image: matrixdotorg/synapse:latest
    container_name: matrix-synapse
    restart: unless-stopped
    depends_on:
      matrix-db:
        condition: service_healthy
    volumes:
      - /opt/matrix-data/synapse:/data
      - /opt/matrix-config/homeserver.yaml:/data/homeserver.yaml:ro
      - /opt/matrix-config/log.config:/data/log.config:ro
    ports:
      - "8008:8008"
      - "8448:8448"
    networks:
      - matrix-net

networks:
  matrix-net:
    driver: bridge
COMPOSEEOF

# Generate signing key and run first-time setup
docker run --rm \
  -v /opt/matrix-data/synapse:/data \
  -v /opt/matrix-config/homeserver.yaml:/data/homeserver.yaml:ro \
  -e SYNAPSE_SERVER_NAME="${matrix_server_name}" \
  -e SYNAPSE_REPORT_STATS=no \
  matrixdotorg/synapse:latest generate

# Start services
docker compose -f /opt/docker-compose.matrix.yml up -d

# Enable restart on reboot via systemd
cat > /etc/systemd/system/matrix.service << 'SVCEOF'
[Unit]
Description=Hub Matrix Server
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStart=/usr/local/lib/docker/cli-plugins/docker-compose -f /opt/docker-compose.matrix.yml up
ExecStop=/usr/local/lib/docker/cli-plugins/docker-compose -f /opt/docker-compose.matrix.yml down
WorkingDirectory=/opt

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl enable matrix
