#!/bin/sh
set -eu

umask 077

: "${MATRIX_MAS_DB_HOST:?MATRIX_MAS_DB_HOST required}"
: "${MATRIX_MAS_DB_PASSWORD:?MATRIX_MAS_DB_PASSWORD required}"
: "${MATRIX_MAS_SYNAPSE_SHARED_SECRET:?MATRIX_MAS_SYNAPSE_SHARED_SECRET required}"
: "${MATRIX_MAS_ENCRYPTION_SECRET:?MATRIX_MAS_ENCRYPTION_SECRET required}"
: "${MATRIX_MAS_SIGNING_KEY:?MATRIX_MAS_SIGNING_KEY required}"
: "${MATRIX_MAS_RDS_CA_FILE:=/etc/ssl/certs/aws-rds-global-bundle.pem}"
: "${MATRIX_MAS_PUBLIC_BASE:=https://auth.zenith-research.ca/}"
: "${MATRIX_MAS_SYNAPSE_ENDPOINT:=https://synapse.zenith-research.ca}"

case "$MATRIX_MAS_ENCRYPTION_SECRET" in
  *[!0-9a-fA-F]*|'')
    echo "MAS encryption secret must be hex encoded" >&2
    exit 1
    ;;
esac
if [ "${#MATRIX_MAS_ENCRYPTION_SECRET}" -ne 64 ]; then
  echo "MAS encryption secret must encode exactly 32 bytes" >&2
  exit 1
fi
case "$MATRIX_MAS_SIGNING_KEY" in
  *"BEGIN "*"PRIVATE KEY"*) ;;
  *)
    echo "MAS signing key must be a PEM private key" >&2
    exit 1
    ;;
esac

run_dir=/tmp/mas
matrix_kind=synapse_read_only
if [ "${MATRIX_MAS_CUTOVER_COMPLETE:-false}" = "true" ]; then
  matrix_kind=synapse
fi
mkdir -p "$run_dir"
printf '%s' "$MATRIX_MAS_DB_PASSWORD" > "$run_dir/database-password"
printf '%s' "$MATRIX_MAS_SYNAPSE_SHARED_SECRET" > "$run_dir/synapse-shared-secret"
printf '%s' "$MATRIX_MAS_ENCRYPTION_SECRET" > "$run_dir/encryption-secret"
printf '%s\n' "$MATRIX_MAS_SIGNING_KEY" > "$run_dir/signing-key.pem"
chmod 600 "$run_dir"/*

cat > "$run_dir/config.yaml" <<EOF
http:
  public_base: ${MATRIX_MAS_PUBLIC_BASE}
  issuer: ${MATRIX_MAS_PUBLIC_BASE}
  trusted_proxies:
    - ${MATRIX_MAS_VPC_CIDR:-10.0.0.0/8}
  listeners:
    - name: web
      resources:
        - name: discovery
        - name: human
        - name: oauth
        - name: compat
        - name: graphql
        - name: assets
      binds:
        - address: "0.0.0.0:8080"
    - name: health
      resources:
        - name: health
      binds:
        - address: "0.0.0.0:8081"
database:
  host: ${MATRIX_MAS_DB_HOST}
  port: 5432
  username: mas
  password_file: ${run_dir}/database-password
  database: mas
  ssl_mode: verify-full
  ssl_ca_file: ${MATRIX_MAS_RDS_CA_FILE}
  min_connections: 1
  max_connections: 10
  connect_timeout: 15
matrix:
  kind: ${matrix_kind}
  homeserver: synapse.zenith-research.ca
  endpoint: ${MATRIX_MAS_SYNAPSE_ENDPOINT}
  secret_file: ${run_dir}/synapse-shared-secret
secrets:
  encryption_file: ${run_dir}/encryption-secret
  keys:
    - key_file: ${run_dir}/signing-key.pem
passwords:
  enabled: true
  schemes:
    - version: 1
      algorithm: bcrypt
      unicode_normalization: true
    - version: 2
      algorithm: argon2id
account:
  password_registration_enabled: false
  password_recovery_enabled: false
  login_with_email_allowed: false
oauth:
  device_code_grant_enabled: true
  device_code_user_code_auto_fill_enabled: false
branding:
  service_name: Hypha
EOF
chmod 600 "$run_dir/config.yaml"

unset MATRIX_MAS_DB_PASSWORD MATRIX_MAS_SYNAPSE_SHARED_SECRET MATRIX_MAS_ENCRYPTION_SECRET MATRIX_MAS_SIGNING_KEY
if [ "$#" -gt 0 ]; then
  exec /usr/local/bin/mas-cli "$@" --config "$run_dir/config.yaml"
fi
exec /usr/local/bin/mas-cli --config "$run_dir/config.yaml" server
