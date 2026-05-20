# Matrix deployment parity

Project F makes Matrix/Synapse a first-class deployment surface beside Hub's AWS ECS services and local Docker stack. This checkpoint is intentionally non-deploying: it documents the target contract and adds static checks so future Matrix work does not regress into local-only or template-mutating behavior.

## Current deployment surfaces

### Local Docker Compose

Files:

- `infra/matrix/docker-compose.yml`
- `infra/matrix/config/homeserver.yaml`
- `infra/matrix/appservices/*.yaml`
- `scripts/setup_matrix_bots.sh`
- `scripts/setup_matrix_sophia.sh`
- `scripts/start.sh`

Contract:

- tracked `infra/matrix/config/homeserver.yaml` remains a template;
- tracked template keeps `app_service_config_files: []`;
- Synapse container renders `/data/homeserver.rendered.yaml` at startup;
- generated appservice registrations go to `/data/appservices/*.resolved.yaml`;
- `/data` ownership is repaired before Synapse starts;
- gateway/bridge appservices are registered only when their token env is present;
- Sophia appservice is opt-in through `MATRIX_REGISTER_SOPHIA_APP_SERVICE=true` and must start the `ingest` receiver too.

### AWS EC2 Matrix module

Files:

- `infra/matrix/aws/main.tf`
- `infra/matrix/aws/variables.tf`
- `infra/matrix/aws/user_data.sh.tpl`
- `infra/matrix/aws/outputs.tf`
- `infra/matrix/aws/terraform.tfvars.example`

Current posture:

- EC2 + encrypted EBS remains the simplest stateful Synapse shape;
- the module is separate from `infra/aws_baseline_80` and has not been adopted into the production Hub ECS Terraform state;
- secrets are still variable-driven and must stay in ignored tfvars or a future Secrets Manager/SSM path;
- use this as self-hosted/cloud Matrix baseline, not as an automatic Hub prod deploy yet.

## Data source of truth

Matrix data classes:

- Postgres database: room/event/account state;
- media store: uploaded media;
- signing key: server identity;
- appservice tokens: bridge/gateway/Sophia auth boundary;
- homeserver config: source template plus runtime-rendered config.

Source-of-truth rules:

- local Docker volumes are local-only unless explicitly exported;
- cloud/self-hosted Matrix must have its own backup/restore path before it is considered durable;
- never commit generated appservice registrations, `.env`, DB files, signing keys, or media state;
- do not treat Matrix `/health` alone as appservice-delivery proof.

## Required smoke tests

Local/static smoke:

```bash
python3 scripts/matrix_deployment_check.py
```

Local runtime smoke when Docker is healthy and the user accepts using local volumes:

```bash
MATRIX_DB_PASSWORD=dummy \
MATRIX_SERVER_NAME=localhost \
MATRIX_REGISTRATION_SECRET=dummy \
MATRIX_MACAROON_SECRET=dummy \
MATRIX_FORM_SECRET=dummy \
  docker compose -f infra/matrix/docker-compose.yml config --quiet

curl -fsS "http://localhost:${MATRIX_HTTP_PORT:-8008}/health"
docker inspect matrix-synapse --format '{{.State.Health.Status}}'
docker logs --tail=120 matrix-synapse 2>&1 | grep -i 'PermissionError' && exit 1 || true
```

Appservice delivery smoke:

- gateway bot can call `/account/whoami` with its appservice token;
- bridge bot can join or receive messages in the feedback room;
- if Sophia is enabled, `ingest` is running and Sophia `/account/whoami` succeeds;
- a feedback-room message reaches the Hub queue/eventbus path.

Cloud/self-hosted smoke:

- DNS resolves to the Matrix host;
- client API health works on the intended public/private URL;
- federation port is open only when federation is intended;
- backup job/snapshot exists for DB/media/signing key;
- appservice delivery test passes, not just Synapse health.

## Backup / restore minimum

Before declaring any Matrix deployment durable:

1. database backup exists and restore is tested;
2. media store backup exists and restore is tested;
3. signing key backup exists offline/secret-managed;
4. appservice tokens can be rotated and re-rendered without mutating tracked templates;
5. recovery docs identify whether the target is local-dev, self-hosted-single-node, cloud-aws-staging, or cloud-aws-prod.

## Open decisions before production Matrix

- Public/federated Matrix vs private community-only Matrix.
- Canonical server name/domain.
- Whether AWS Matrix should remain EC2+EBS or move to another stateful runtime.
- Whether appservice secrets should move from env/tfvars to Secrets Manager/SSM.
- Backup target and retention.
- Whether Matrix joins Hub's prod VPC or remains a separate deployment profile.
