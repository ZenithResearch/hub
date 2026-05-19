<!-- PROD_READINESS_PROJECT_A -->
# Hub Production Readiness and Deployment Notes

This document is the operator runbook for production readiness checks, deployment access paths, and drift-safe Hub operations.

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

1. Public Hub health
   - command: `python3 scripts/prod_smoke.py --target prod --mode public`
   - proves: public gateway is reachable.

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

# Agent Platform — AWS Baseline (~$80/mo)

Baseline targets ~100 DAU with a simple always-on footprint:

- 1× `gateway-http` task (0.25 vCPU / 0.5 GB) **public** behind ALB
- 1× `runtime-grpc` task (0.25 vCPU / 0.5 GB) **private only**
- 1× `tool-sandbox` task (0.25 vCPU / 0.5 GB) **private only**
- 1× Application Load Balancer (HTTP :80)
- 1× NAT Gateway (single) for private task egress
- External vector store: **Qdrant Cloud** (not provisioned here)

This folder contains the Terraform for the baseline plus a deployment workflow that requires **no application code changes** (config-only via env vars and a secret).

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
  -backend-config="dynamodb_table=$LOCK_TABLE" \
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

## 4) Build & push images to ECR (one per service)

This baseline creates 3 ECR repos. You can build the image once and push it to all three repos using the same tag.

```bash
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

GW_REPO="$(terraform output -raw ecr_gateway_repo_url)"
RT_REPO="$(terraform output -raw ecr_runtime_repo_url)"
SB_REPO="$(terraform output -raw ecr_sandbox_repo_url)"

IMAGE_TAG="$(grep -E '^image_tag' terraform.tfvars | awk -F'\"' '{print $2}')"
test -n "$IMAGE_TAG" || IMAGE_TAG="latest"

docker build -t agent-platform:"$IMAGE_TAG" ../..

docker tag agent-platform:"$IMAGE_TAG" "$GW_REPO:$IMAGE_TAG"
docker tag agent-platform:"$IMAGE_TAG" "$RT_REPO:$IMAGE_TAG"
docker tag agent-platform:"$IMAGE_TAG" "$SB_REPO:$IMAGE_TAG"

docker push "$GW_REPO:$IMAGE_TAG"
docker push "$RT_REPO:$IMAGE_TAG"
docker push "$SB_REPO:$IMAGE_TAG"
```

---

## 5) Deploy / update ECS services to the new image tag

If you changed `image_tag` in `terraform.tfvars`, apply again:

```bash
terraform apply
```

If you re-pushed the same tag (not recommended for production), force a new deployment:

```bash
CLUSTER="$(terraform output -raw ecs_cluster_name)"
GW_SVC="$(terraform output -raw gateway_service_name)"
RT_SVC="$(terraform output -raw runtime_service_name)"
SB_SVC="$(terraform output -raw sandbox_service_name)"

aws ecs update-service --cluster "$CLUSTER" --service "$GW_SVC" --force-new-deployment
aws ecs update-service --cluster "$CLUSTER" --service "$RT_SVC" --force-new-deployment
aws ecs update-service --cluster "$CLUSTER" --service "$SB_SVC" --force-new-deployment
```

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

1) Check ALB health:

```bash
ALB_DNS="$(terraform output -raw alb_dns_name)"
curl -sS "http://$ALB_DNS/health"
```

2) Send a message:

```bash
curl -sS -X POST "http://$ALB_DNS/v1/messages" \
  -H "content-type: application/json" \
  -d '{"user_id":"user-1","session_id":"sess-1","message":"Hello"}'
```

3) Confirm logs in CloudWatch Logs:

- `/ecs/<project>-<env>/gateway-http`
- `/ecs/<project>-<env>/runtime-grpc`
- `/ecs/<project>-<env>/tool-sandbox`

---

## Rollback

Roll back by reverting the `image_tag` to a previous immutable tag and running:

```bash
terraform apply
```

Or force-deploy the prior tag by updating the task definition revision and redeploying (ECS console / CLI).

---

## WebSockets / SSE stability notes

This baseline sets **ALB idle timeout** via `alb_idle_timeout_seconds` (default 3600s). For WebSockets/SSE:

- Increase ALB idle timeout above your heartbeat interval
- Use application-level heartbeats/ping/pong to keep connections active
- Scale gateway on CPU/memory first; later add an active-connection metric

---

## Cost notes (~$80/month baseline)

This baseline is designed to keep fixed monthly costs low. Primary cost drivers:

- **NAT Gateway** (hourly + data processing)
- **ALB** (hourly + LCUs)
- **Fargate** (3 always-on tasks at 0.25 vCPU / 0.5GB)

Secondary cost drivers:

- CloudWatch Logs ingestion/retention
- ECR storage
- Secrets Manager (per secret + API calls)

Qdrant Cloud is billed separately.

