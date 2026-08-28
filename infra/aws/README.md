# AWS (ECS/Fargate) — Gateway + Runtime + Tool Sandbox

This guide sets up a production-style baseline for **many external chat users**:

- **CloudFront + WAF + ALB** → `gateway-http` (public)
- `runtime-grpc` and `tool-sandbox` are **private** (no public listeners / no public IPs)
- All services run on **ECS Fargate**
- **Service discovery** via **AWS Cloud Map**
- **Strict security groups** (ALB → gateway only; gateway → runtime only; runtime → tool-sandbox only)

> CloudFront can work with WebSockets/SSE, but configuration and testing matter for long-lived connections. Keep idle timeouts aligned and use app-level heartbeats/pings.

---

## Quickstart (Edge default)

If you’re setting this up from scratch, follow [`doc/setup.md`](../../doc/setup.md) first (AWS credentials, `.env.local`, Apple Silicon notes).

Required inputs (minimal):
- `AWS_REGION`
- Terraform state backend: `STATE_BUCKET`, `LOCK_TABLE` (recommended for teams)
- `QDRANT_URL` (Qdrant Cloud)
- `IMAGE_TAG` (container tag to deploy)

Recommended additional input:
- `QDRANT_API_KEY` (store in Secrets Manager; referenced by ARN in Terraform)

The repo also includes Make targets that automate these steps (see `Makefile`).

### Option A (recommended): `.env.local`

From the repo root, copy the template and edit values:

```bash
cp .env.local.example .env.local
# edit .env.local
```

Then deploy:

```bash
make doctor
make aws-edge-up
```

### Option B: one-off exports

Example:

```bash
export AWS_REGION=us-west-2
export STATE_BUCKET="agent-platform-tf-state-yourname-20260223"
export LOCK_TABLE="agent-platform-tf-locks"

export QDRANT_URL="https://your-qdrant-cloud-url"
# Load QDRANT_API_KEY from an approved local secret store, then export the
# existing environment value without placing it in this file or shell history.
export QDRANT_API_KEY

export IMAGE_TAG="v0.1.0"
# Optional: override the ECR repo name used for the shared image
# export REPO_NAME="agent-platform"
# Apple Silicon note: build an amd64 image for ECS/Fargate
# export DOCKER_DEFAULT_PLATFORM=linux/amd64

make doctor
make aws-edge-up
```

---

## Prerequisites

For the current operator-controlled AWS production inventory without the
CloudFront edge profile, see `infra/aws_baseline_80/DEPLOYMENT.md`. The legacy
directory name is not a current cost or capacity claim.

- AWS account + credentials with permissions for: VPC, ECS, ALB, IAM, WAFv2, CloudWatch Logs, Route53 (optional), ACM (TLS)
- Docker (to build/push the image)
- Terraform (recommended >= 1.6)
- An external Qdrant endpoint (recommended: Qdrant Cloud) + API key (optional)

---

## 1) Build & push the container image (ECR)

All services use the **same image**, with different commands per ECS service.

If you are using the repo’s Make targets, this step is:

```bash
make aws-edge-push
```

```bash
AWS_REGION=us-west-2
AWS_ACCOUNT_ID=123456789012
REPO_NAME=agent-platform

aws ecr create-repository --repository-name "$REPO_NAME" --region "$AWS_REGION" || true
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build -t "$REPO_NAME:latest" .
docker tag "$REPO_NAME:latest" "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO_NAME:latest"
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO_NAME:latest"
```

---

## 2) TLS certificates (ACM)

### CloudFront (default TLS)

By default, CloudFront can use a built-in TLS certificate and you can access the gateway at:
`https://<distribution>.cloudfront.net` (no custom domain required).

### Optional: custom domain on CloudFront

If you want a custom domain, create/import a certificate in **us-east-1** and pass `cloudfront_acm_cert_arn`, along with `domain_name` + `route53_zone_id`.

### Optional: ALB HTTPS (not required for the default edge)

Create or import a certificate in the **same region** you deploy the ALB (e.g. `us-west-2`).

- You will pass this as `acm_cert_arn` when `enable_https=true`.

---

## 3) (Optional) Store Qdrant API key in Secrets Manager

If you use Qdrant Cloud with an API key, create a secret and pass its ARN as `qdrant_api_key_secret_arn`.

If you are using the repo’s Make targets, this step is:

```bash
make aws-edge-secret
```

```bash
aws secretsmanager create-secret \
  --name "agent-platform/qdrant_api_key" \
  --secret-string "YOUR_QDRANT_API_KEY" \
  --region "$AWS_REGION"
```

---

## 4) Terraform apply

If you are using the repo’s Make targets, this step is:

```bash
make aws-edge-backend
make aws-edge-apply
```

```bash
cd infra/aws/terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars
terraform init \
  -backend-config="bucket=$STATE_BUCKET" \
  -backend-config="key=agent-platform/aws-edge/terraform.tfstate" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="dynamodb_table=$LOCK_TABLE" \
  -backend-config="encrypt=true"
terraform apply
```

Outputs include:
- `gateway_public_url`
- `ecs_cluster_name`
- `kb_indexer_task_definition_arn`
- `private_subnet_ids`

---

## 5) Seed the Knowledge Base (one-shot ECS task)

This runs the `kb-indexer` task definition on Fargate.

If you are using the repo’s Make targets, this step is:

```bash
make aws-edge-seed
```

```bash
CLUSTER_NAME="$(terraform output -raw ecs_cluster_name)"
TASK_DEF_ARN="$(terraform output -raw kb_indexer_task_definition_arn)"
SUBNETS="$(terraform output -json private_subnet_ids | jq -r 'join(",")')"
SG_ID="$(terraform output -raw kb_indexer_security_group_id)"

aws ecs run-task \
  --cluster "$CLUSTER_NAME" \
  --launch-type FARGATE \
  --task-definition "$TASK_DEF_ARN" \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG_ID],assignPublicIp=DISABLED}"
```

---

## WebSockets / SSE: stability checklist (don’t get random drops)

### 1) ALB idle timeout

Increase ALB idle timeout (Terraform variable: `alb_idle_timeout_seconds`). For long-lived sockets, **3600s** is a common starting point.

### 2) App keepalive / heartbeat

WebSockets and SSE streams should send periodic traffic (ping/pong or heartbeats) to avoid proxy/LB idling out connections.

- This is transport health, not business logic.
- Align heartbeat interval with `alb_idle_timeout_seconds`.

### 3) ECS scaling

Each connection consumes memory and file descriptors.

Baseline autoscaling can use CPU/memory, but for WebSockets you’ll eventually want a custom metric (active connections).

### 4) Sticky sessions

WebSockets are inherently “sticky” per connection. Reconnects may land on another task; clients should tolerate reconnects.

---

## CloudFront considerations (edge default)

CloudFront can work with WebSockets/SSE, but it adds moving parts:
- ensure upgrade headers are forwarded
- keep CloudFront/ALB/app idle timeouts aligned
- test long-lived connections under load

If you want fewer moving parts, you can disable CloudFront by setting `enable_cloudfront=false` and leaving WAF + ALB directly in front of `gateway-http`.
