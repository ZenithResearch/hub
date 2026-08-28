<!-- PROD_READINESS_PROJECT_A -->
# Hub Production Readiness and Deployment Notes

This document is the operator runbook for production readiness checks, deployment access paths, and drift-safe Hub operations.

It describes the current AWS substrate, not the approved private Hub target. The
public Hub URL, public smoke, Gateway operator path, and profile-specific Matrix
routes bypass secS-magik. They remain useful for current operations but do not
establish target capability maturity. The migration contract is
[`../../docs/architecture/private-exposure-boundary.md`](../../docs/architecture/private-exposure-boundary.md).

Current production target:

- AWS profile: `zenith-hermes`
- AWS region: `us-east-1`
- ECS cluster: `zenith-hub-prod-cluster`
- Public Hub URL: `https://hub.zenith-research.ca`
- Internal service discovery namespace: `zenith-hub-prod.local`

Safety rules:

- Do not print tokens, review codes, DB passwords, API keys, session tokens, or access-code hashes.
- Read admin/operator credentials from environment, macOS Keychain, AWS Secrets Manager, or GitHub environment secrets only.
- Do not run broad production `terraform apply` until live drift is codified/imported or explicitly marked script-managed.
- Do not reset local Docker data until production access, backup/export, CI, deploy, and local-recreate paths are verified.
- Do not build images that bake multi-GB GGUF model files from the local laptop.

---

## Standard clean-main rollout

For normal app/source updates, use the repository-level rollout runbook rather than direct ECS updates:

- `docs/operations/production-rollout.md` — clean `main`, CI, immutable image build, Terraform plan/apply, ECS stability, smoke.
- `docs/operations/operator-updates.md` — operator-owned deployment doctrine and planner boundaries.

This AWS note remains the production inventory/smoke reference. If the rollout plan and this file disagree, treat `docs/operations/production-rollout.md` as the current command sequence and update this file.

---

## Project A readiness smoke

Run the smoke script from the Hub repo root:

```bash
python3 scripts/prod_smoke.py --target prod --mode public
```

Public mode checks only unauthenticated public endpoints and is safe to run without credentials.

Operator mode checks admin/cases/queue reachability. It reads the admin token from `REVIEW_ACCESS_ADMIN_TOKEN` or macOS Keychain service `zenith-hub-review-access-admin-token` and never prints the value:

```bash
python3 scripts/prod_smoke.py --target prod --mode operator
```

Internal mode checks ECS service state for the expected production services:

```bash
python3 scripts/prod_smoke.py --target prod --mode internal
```

Internal private endpoint probes are opt-in because they run a one-off ECS task in the production VPC:

```bash
python3 scripts/prod_smoke.py --target prod --mode internal --run-internal-probes
```

Expected output shape:

```json
{
  "files": ["scripts/prod_smoke.py", "infra/aws_baseline_80/DEPLOYMENT.md"],
  "tests": [],
  "deploy": {},
  "blocker": "none",
  "next": "..."
}
```

If any check fails, the script exits non-zero and sets `blocker` to the failing check names.

---

## Production service inventory to preserve

Expected production services:

- `zenith-hub-prod-gateway-http`
- `zenith-hub-prod-runtime-grpc`
- `zenith-hub-prod-tool-sandbox`
- `zenith-hub-prod-queue`
- `zenith-hub-prod-cases`
- `zenith-hub-prod-eventbus`
- `zenith-hub-prod-stt-http`
- `zenith-hub-prod-frank`
- `zenith-hub-prod-llama-server`

Known current production contracts:

- STT must stay at or above CPU `1024`, memory `2048`; lower memory previously caused OOM during Whisper transcription.
- Frank uses `FRANK_RUNTIME=native_case_pipeline`.
- Frank model-backed paths use `FRANK_MODEL=Qwen3.5-9B-Q4_K_M.gguf` and `OPENAI_BASE_URL=http://llama-server.zenith-hub-prod.local:3690/v1`.
- Internal llama-server auth uses no bearer token / none-equivalent; do not add a raw API key for this internal endpoint.
- Llama-server is private/internal only and should not get public ALB ingress.
- The Qwen GGUF model is staged through private S3 -> one-shot ECS preload -> EFS -> read-only task mount, not through a local-built Docker image.

---

## Data access paths to prove before Docker reset

Before treating local Docker volumes as disposable, verify and document these paths:

1. Current legacy public Hub health
   - command: `python3 scripts/prod_smoke.py --target prod --mode public`
   - proves: the current public Gateway is reachable; it does not prove secS-only
     admission or private-boundary conformance.

2. Gateway admin/cases/queue reachability
   - command: `python3 scripts/prod_smoke.py --target prod --mode operator`
   - credential source: `REVIEW_ACCESS_ADMIN_TOKEN` env var or macOS Keychain service `zenith-hub-review-access-admin-token`
   - proves: operator can inspect cases and queue without exposing credentials.

3. ECS service state
   - command: `python3 scripts/prod_smoke.py --target prod --mode internal`
   - proves: required ECS services are ACTIVE and desired/running counts match.

4. Internal STT and llama-server reachability
   - command: `python3 scripts/prod_smoke.py --target prod --mode internal --run-internal-probes`
   - proves: private Hub services can reach STT and llama-server through service discovery.

5. Review-auth database access
   - source of truth: production Postgres/RDS clients registry.
   - access path: approved in-VPC/admin path only; do not ferry DB contents through S3 or local files without explicit approval.
   - proof to add: redacted session-auth smoke or safe row-count/query that does not print codes/hashes/tokens.

6. Cases/runs/logs access
   - source of truth: production cases service/storage.
   - access path: Gateway admin cases endpoint or internal cases service through approved operator path.
   - proof to add: redacted case list/detail smoke.

7. Review artifacts and model artifacts
   - source of truth: production storage/EFS/S3 path used by runtime.
   - model path to preserve: `/data/llama/Qwen3.5-9B-Q4_K_M.gguf` on EFS, mounted read-only as `/models/llama/Qwen3.5-9B-Q4_K_M.gguf`.
   - proof to add: size/hash/path check without printing credentials.

### Llama-server model staging

The Qwen GGUF model artifact is staged through private S3 and a one-shot ECS preload task, not by baking the multi-GB model into a Docker image.

1. Upload the local GGUF to the private model bucket and preload it into Frank EFS:

```bash
python3 scripts/stage_llama_model.py \
  --workdir . \
  --upload-local /path/to/Qwen3.5-9B-Q4_K_M.gguf \
  --expected-sha256 03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8
```

2. If the object is already in S3, run only the private ECS preload task:

```bash
python3 scripts/stage_llama_model.py --workdir . --expected-sha256 03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8
```

3. Verify llama-server after staging:

```bash
python3 scripts/prod_smoke.py --target prod --mode internal --run-internal-probes
```

The preload task mounts Frank EFS writable at `/models`, downloads `s3://<bucket>/models/Qwen3.5-9B-Q4_K_M.gguf`, verifies SHA256 when provided, and atomically moves the artifact into `/models/llama/Qwen3.5-9B-Q4_K_M.gguf`. The llama-server service mounts the same EFS path read-only.

8. Matrix data if Matrix local volumes are reset
   - source of truth must be explicit before reset: Matrix DB, media, signing keys, and appservice registrations.
   - generated runtime config belongs under `/data`, not tracked templates.

---

## Rollback and drift warnings

- If production smoke fails after a deploy, roll back to the previous ECS task definition before broad debugging.
- If a Terraform plan wants to reduce STT CPU/memory below `1024/2048`, stop.
- If a Terraform plan wants to remove or publicly expose `zenith-hub-prod-llama-server`, stop.
- If a Terraform plan wants to mutate durable RDS/EFS/S3 resources unexpectedly, stop.
- If a deploy path requires local Docker Desktop to build production images, stop and move the build to CI/CodeBuild.

---

# Additional AWS baseline setup reference

This section contains shared backend, secret, and Terraform setup details. The
production service inventory earlier in this document is authoritative; do not
infer service count, capacity, cost, or readiness from the directory name.

The `aws_baseline_80` path is retained for Terraform state, scripts, tests, and
operator compatibility. It is not a current product-sizing or pricing label.

If you need a full edge setup (CloudFront + WAF + ALB), use the AWS edge path in `infra/aws/terraform` instead.

---

## Prerequisites

- AWS CLI v2 authenticated (`aws sts get-caller-identity`)
- Terraform >= 1.6
- Docker
- Qdrant Cloud cluster URL + API key

---

## 1) Create Terraform state backend (S3 + DynamoDB)

Pick a unique bucket name.

```bash
AWS_REGION=us-west-2
STATE_BUCKET="agent-platform-tf-state-<unique>"
LOCK_TABLE="agent-platform-tf-locks"

aws s3api create-bucket \
  --bucket "$STATE_BUCKET" \
  --region "$AWS_REGION" \
  --create-bucket-configuration LocationConstraint="$AWS_REGION"

aws s3api put-bucket-versioning \
  --bucket "$STATE_BUCKET" \
  --versioning-configuration Status=Enabled

aws dynamodb create-table \
  --table-name "$LOCK_TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$AWS_REGION"
```

---

## 2) Terraform apply

```bash
cd infra/aws_baseline_80
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars (aws_region, qdrant_url, image_tag, cors_allow_origins, etc.)

terraform init \
  -backend-config="bucket=$STATE_BUCKET" \
  -backend-config="key=agent-platform/${AWS_REGION}/baseline80.tfstate" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="use_lockfile=true" \
  -backend-config="encrypt=true"

terraform plan
terraform apply
```

Note: this creates the Secrets Manager secret **container**, but does not require you to store the API key in Terraform state.

---

## 3) Set the Qdrant API key (Secrets Manager)

Get the secret ARN (Terraform output):

```bash
QDRANT_SECRET_ARN="$(terraform output -raw qdrant_api_key_secret_arn)"
```

Set the secret value without putting it in Terraform state:

```bash
aws secretsmanager put-secret-value \
  --secret-id "$QDRANT_SECRET_ARN" \
  --secret-string "YOUR_QDRANT_API_KEY" \
  --region "$AWS_REGION"
```

If you *do* set `qdrant_api_key` in `terraform.tfvars`, Terraform will manage the secret version, but the API key will be stored in TF state.

---

## 4) Build and publish immutable service images

Use `docs/operations/production-rollout.md` and `scripts/prod_build_image.sh`.
The current Terraform defines gateway, runtime, sandbox, and queue repositories;
additional Hub services may use service-specific tags from the gateway repository.
Do not rebuild production images from an unverified local checkout or infer the
deployed service graph from the ECR repository count.

---

## 5) Deploy or update ECS services

Set immutable global or service-specific image tags, then use the reviewed
operator-controlled plan/apply flow in `scripts/prod_terraform_cd.sh`. Do not
force-update an assumed subset of ECS services as the normal rollout path.

---

## Environment variable mapping

Inside AWS, service-to-service calls use Cloud Map names:

- gateway → runtime: `RUNTIME_GRPC_TARGET = <terraform output runtime_grpc_target>`
- runtime → sandbox: `TOOL_SANDBOX_GRPC_TARGET = <terraform output tool_sandbox_grpc_target>`

Runtime’s Qdrant config:

- `QDRANT_URL` is a plain env var (Terraform var `qdrant_url`)
- `QDRANT_API_KEY` is injected from Secrets Manager into the runtime task definition

Reference file: `.env.aws.example`

---

## Verification

Use `scripts/prod_smoke.py` in public, operator, and internal modes as described
earlier. Review ECS stability and CloudWatch logs for every service changed by
the Terraform plan, not only the original core subset.

---

## Project E: operator-controlled production rollout

Production rollout is intentionally not a GitHub Actions CD workflow. GitHub Actions may build immutable images and run CI, but the live Hub node is updated by a local/operator-controlled Terraform plan/apply so failed or intentionally incomplete GitHub CD runs do not present as misleading deployment failures.

Use the local helper from an authenticated operator machine:

```bash
AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 \
  PROD_TFVARS_PATH=/path/to/local/prod/terraform.tfvars \
  GATEWAY_IMAGE_TAG=<new-gateway-tag> \
  FRANK_IMAGE_TAG=<new-frank-tag-or-current-live-tag> \
  EVENTBUS_IMAGE_TAG=<current-live-eventbus-tag> \
  CASES_IMAGE_TAG=<current-live-cases-tag> \
  STT_IMAGE_TAG=<current-live-stt-tag> \
  scripts/prod_terraform_cd.sh plan
```

Review the saved plan text before any apply. Expected source-code rollouts usually replace only the intended ECS task definitions/services while preserving unaffected service tags.

Only use `scripts/prod_terraform_cd.sh apply` after reviewing the saved plan text and confirming the production change window.

---

## Rollback

Roll back by reverting the `image_tag` or service-specific image tag override to a previous immutable tag and running a reviewed production plan/apply through `scripts/prod_terraform_cd.sh` from an authenticated operator machine.

For urgent ECS-only rollback, update the affected service back to the prior task definition revision in ECS, then codify that revision/tag in Terraform immediately after the incident.

---

## WebSockets / SSE stability notes

This baseline sets **ALB idle timeout** via `alb_idle_timeout_seconds` (default 3600s). For WebSockets/SSE:

- Increase ALB idle timeout above your heartbeat interval
- Use application-level heartbeats/ping/pong to keep connections active
- Scale gateway on CPU/memory first; later add an active-connection metric

---
