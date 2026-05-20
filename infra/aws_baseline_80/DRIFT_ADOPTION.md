# Hub AWS Baseline Drift Adoption Plan

> Project C0 output. This is an inspect-only adoption plan for reconciling live production resources with Terraform/IaC. Do not run broad `terraform apply` until the adoption decisions below are implemented and a reviewed plan proves no destructive rollback.

## Scope

Production target inspected:

- AWS profile: `zenith-hermes`
- Region: `us-east-1`
- ECS cluster: `zenith-hub-prod-cluster`
- Public Hub: `https://hub.zenith-research.ca`
- Private namespace: `zenith-hub-prod.local`

This document records live drift and how each resource should enter IaC:

- `terraform import existing` — live resource should be brought under Terraform state before apply.
- `codify and compare` — source should define expected shape, then `terraform plan` decides whether import/replace is safe.
- `script-managed intentionally` — keep as runbook/script for now, but document it.
- `safe to replace` — can be recreated without durable-data risk.

## Hard stop rules

Stop before apply if a plan would:

- reduce STT below CPU `1024` / memory `2048`;
- clear Frank `FRANK_MODEL` or `OPENAI_BASE_URL`;
- remove or publicly expose `zenith-hub-prod-llama-server`;
- replace/delete RDS, EFS, S3 model objects, or other durable state unexpectedly;
- depend on local Docker Desktop to build production images;
- print or commit secrets/API keys/tokens.

## Live service inventory

| Service | Live task def | CPU | Memory | Desired/running | Adoption decision | Notes |
|---|---:|---:|---:|---:|---|---|
| gateway-http | `zenith-hub-prod-gateway-http:10` | 256 | 512 | 1/1 | codify and compare | Public ALB service; EFS-backed gateway data. |
| runtime-grpc | `zenith-hub-prod-runtime-grpc:3` | 256 | 512 | 1/1 | codify and compare | Existing Terraform surface likely closest to current source. |
| tool-sandbox | `zenith-hub-prod-tool-sandbox:3` | 256 | 512 | 1/1 | codify and compare | Existing Terraform surface likely closest to current source. |
| queue | `zenith-hub-prod-queue:3` | 256 | 512 | 1/1 | codify and compare | EFS-backed queue data; dirty TF appears to add queue ECR/output/service support. |
| cases | `zenith-hub-prod-cases:2` | 256 | 512 | 1/1 | codify and compare, then import if resource absent from state | Uses hotfix image lineage; EFS-backed cases data. |
| eventbus | `zenith-hub-prod-eventbus:1` | 256 | 512 | 1/1 | codify and compare, then import if resource absent from state | HTTP 8082 service discovery. |
| stt-http | `zenith-hub-prod-stt-http:4` | 1024 | 2048 | 1/1 | import existing/codify before apply | Live sizing is critical; prior 256/512 caused OOM. |
| frank | `zenith-hub-prod-frank:7` | 256 | 512 | 1/1 | import existing/codify before apply | Must preserve Qwen env and EFS write mount. |
| llama-server | `zenith-hub-prod-llama-server:1` | 4096 | 16384 | 1/1 | codify as new Terraform unit, import existing before apply | Private internal Qwen llama.cpp service; no public ingress. |

## Live task-definition contracts to preserve

### STT HTTP

Live facts:

- task family/revision: `zenith-hub-prod-stt-http:4`
- CPU/memory: `1024` / `2048`
- image: gateway ECR repo with `stt-cache-hotfix-20260519013103` lineage
- env:
  - `STT_ALLOWED_AUDIO_ROOTS=/data/frank_execution`
  - `STT_WHISPER_MODEL=tiny`
  - `STT_ALLOWED_WHISPER_MODELS=tiny,base,small`
- mounts Frank EFS read-only at `/data`
- port `8765`

Adoption decision:

- C1 must add per-service sizing variables and set STT to `1024/2048` before any apply.
- Import/adopt live `aws_ecs_task_definition.stt_http` / `aws_ecs_service.stt_http` if not already in Terraform state.
- Do not rely on shared `var.task_cpu` / `var.task_memory` for STT.

### Frank

Live facts:

- task family/revision: `zenith-hub-prod-frank:7`
- CPU/memory: `256` / `512`
- image: gateway ECR repo with `frank-stt-backoff-hotfix-20260519190823` lineage
- env to preserve:
  - `FRANK_RUNTIME=native_case_pipeline`
  - `CASES_HTTP_URL=http://cases.zenith-hub-prod.local:8083`
  - `STT_HTTP_URL=http://stt-http.zenith-hub-prod.local:8765`
  - `GATEWAY_HTTP_URL=https://hub.zenith-research.ca`
  - `FRANK_MODEL=Qwen3.5-9B-Q4_K_M.gguf`
  - `OPENAI_BASE_URL=http://llama-server.zenith-hub-prod.local:3690/v1`
  - internal llama-server auth uses no real bearer secret / none-equivalent
- mounts Frank EFS read-write at `/data`
- no inbound port required

Adoption decision:

- C2 must codify Frank model env before apply.
- Terraform source currently observed with `FRANK_MODEL` empty in `ecs.tf`; that is unsafe and must be fixed before apply.
- Import/adopt live service/task definition if not already in state.

### Llama server

Live facts:

- ECS service: `zenith-hub-prod-llama-server`
- task family/revision: `zenith-hub-prod-llama-server:1`
- CPU/memory: `4096` / `16384`
- image: `ghcr.io/ggml-org/llama.cpp:server`
- endpoint: `http://llama-server.zenith-hub-prod.local:3690/v1`
- health: `http://llama-server.zenith-hub-prod.local:3690/health`
- port: `3690`
- command must include `--reasoning off`
- model path in task: `/models/llama/Qwen3.5-9B-Q4_K_M.gguf`
- mounts Frank EFS read-only at `/models`
- no public ALB; private service discovery only

Adoption decision:

- C3 should add a dedicated Terraform service/task/log group/SG/Cloud Map resources.
- Import existing ECS service, task definition family, Cloud Map service, SGs/log group if Terraform will own them.
- If task definition import proves too brittle, codify desired future task definition and update service only after a reviewed plan/smoke gate.

## Durable data and storage resources

| Resource/data surface | Live/desired use | Adoption decision | Risk |
|---|---|---|---|
| Gateway EFS | `/data` for gateway/review-auth local data surfaces where still used | import existing if Terraform-managed | durable data; do not replace |
| Queue EFS | queue persistence | import existing if Terraform-managed | durable queue state; do not replace casually |
| Cases EFS | cases DB/runtime state | import existing if Terraform-managed | durable case state; do not replace |
| Frank EFS | Frank execution artifacts, STT audio read path, llama model runtime copy | import existing before apply | critical shared runtime data; do not replace |
| Qwen model S3 object | source model staging artifact | script-managed intentionally for now | large artifact; keep private/versioned |
| EFS model copy | `/data/llama/Qwen3.5-9B-Q4_K_M.gguf` | script-managed preload until C4 | do not delete without replacement model staged |
| RDS/Postgres clients registry | production review auth source of truth | already outside C0 drift scope; preserve | do not ferry through S3/local files without approval |

## Network and service discovery adoption

Live services use private subnets with `assignPublicIp=DISABLED`.

| Surface | Adoption decision | Notes |
|---|---|---|
| Cloud Map namespace `zenith-hub-prod.local` | existing Terraform/import | Preserve. |
| `cases.zenith-hub-prod.local` | import/codify if absent | Required by Frank/Gateway. |
| `eventbus.zenith-hub-prod.local` | import/codify if absent | Required for queue/event dispatch. |
| `stt-http.zenith-hub-prod.local` | import/codify if absent | Required by Frank Step 2. |
| `llama-server.zenith-hub-prod.local` | import/codify before apply | Required by Frank model-backed paths. |
| llama-server SG ingress | preserve via `llama_server_security_group_id` until C3 manages llama-server | Prod plans must pass the live llama-server SG ID or import a managed SG before apply. |
| Frank -> llama-server egress | codify explicit egress on TCP 3690 | Must allow Frank to reach the OpenAI-compatible endpoint. |
| STT -> Frank EFS NFS | codify read-only task mount plus SG NFS ingress | STT reads audio from Frank execution path. |

## Current dirty Terraform assessment

Current dirty Terraform validates, but is not yet safe to apply broadly.

Observed dirty changes add or modify many resources:

- `ecs.tf`: adds cases, eventbus, Frank, STT task/service surfaces.
- `efs.tf`: adds cases and Frank EFS/access-point/security-group surfaces.
- `iam.tf`: adds task roles and EFS policies for cases/Frank/STT.
- `logs.tf`: adds log groups for cases/eventbus/Frank/STT.
- `security_groups.tf`: adds cases/eventbus/Frank/STT SGs.
- `service_discovery.tf`: adds cases/eventbus/STT Cloud Map services.
- `variables.tf`: adds desired counts and image tag overrides, but still keeps shared `task_cpu/task_memory`.
- `outputs.tf`: adds outputs for queue/cases/eventbus/Frank/STT.
- `docker/stt_http/Dockerfile`: dirty outside Terraform; review separately.

Critical gaps after C1/C2 working-tree patches:

1. STT has been moved off shared `var.task_cpu` / `var.task_memory` in the working tree and now uses `stt_http_task_cpu=1024` / `stt_http_task_memory=2048`.
2. Frank model env has been patched in the working tree to preserve `FRANK_MODEL=Qwen3.5-9B-Q4_K_M.gguf` and `OPENAI_BASE_URL=http://llama-server.zenith-hub-prod.local:3690/v1`.
3. Llama-server is not yet represented in the dirty Terraform source.
4. Model staging/preload path is not yet codified.
5. Import/state ownership for existing live resources is not documented in Terraform commands yet.

## Recommended adoption sequence

### C1: Per-service CPU/memory, starting with STT

Files:

- `infra/aws_baseline_80/variables.tf`
- `infra/aws_baseline_80/ecs.tf`
- `infra/aws_baseline_80/terraform.tfvars.example`

Required result:

- STT uses `stt_http_task_cpu = 1024` and `stt_http_task_memory = 2048` or equivalent.
- Existing low-footprint services can keep `256/512`.
- Terraform plan must not resize unrelated services unless explicitly intended.

### C2: Frank model env

Files:

- `infra/aws_baseline_80/ecs.tf`
- `infra/aws_baseline_80/variables.tf` if values become variables
- `infra/aws_baseline_80/terraform.tfvars.example` if values become variables

Required result:

- `FRANK_MODEL=Qwen3.5-9B-Q4_K_M.gguf`
- `OPENAI_BASE_URL=http://llama-server.zenith-hub-prod.local:3690/v1`
- no raw bearer/API key committed.

### C3: Llama-server Terraform unit

Files likely needed:

- `infra/aws_baseline_80/ecs.tf`
- `infra/aws_baseline_80/security_groups.tf`
- `infra/aws_baseline_80/service_discovery.tf`
- `infra/aws_baseline_80/logs.tf`
- `infra/aws_baseline_80/outputs.tf`
- `infra/aws_baseline_80/variables.tf`

Required result:

- private service on port `3690`;
- CPU/memory `4096/16384` or deliberate replacement sizing;
- read-only Frank EFS mount at `/models`;
- command includes `--reasoning off`;
- no public ingress.

### C4: Model staging/preload runbook or script

Files likely needed:

- `infra/aws_baseline_80/DEPLOYMENT.md`
- possibly `scripts/preload_model_to_efs.py`
- possibly Terraform IAM/preload task resources after C3 is stable

Required result:

- operator can stage private S3 model object into EFS without local Docker build;
- path and hash/size checks are documented without secrets.

## Minimum verification before any apply

Run:

```bash
terraform -chdir=infra/aws_baseline_80 fmt -check
terraform -chdir=infra/aws_baseline_80 validate -no-color
terraform -chdir=infra/aws_baseline_80 plan -no-color
python3 scripts/prod_smoke.py --target prod --mode public
python3 scripts/prod_smoke.py --target prod --mode operator
python3 scripts/prod_smoke.py --target prod --mode internal
```

Only run internal private endpoint probes when an ECS one-off probe is acceptable:

```bash
python3 scripts/prod_smoke.py --target prod --mode internal --run-internal-probes
```

## C0 conclusion

Do not apply the current dirty Terraform as-is.

Proceed first with C1 and C2 as narrow commits:

1. C1: per-service sizing with STT fixed at `1024/2048`.
2. C2: Frank model env preserved.

Then handle C3 llama-server as its own Terraform adoption unit with explicit import/state handling.


## C3 working-tree patch: llama-server Terraform adoption

The clean adoption worktree now codifies the live internal llama-server surface as a separate adoption unit. It adds Terraform resources and import blocks for the existing private security group, Cloud Map service, ECS task definition/service, task role/policy, and CloudWatch log group. The service remains private-only on port 3690, uses `ghcr.io/ggml-org/llama.cpp:server`, serves `/models/llama/Qwen3.5-9B-Q4_K_M.gguf`, mounts Frank EFS read-only at `/models`, and keeps CPU/memory at `4096/16384`. No Terraform apply has been run; the import/apply plan must be reviewed before execution.


C3 also splits image tag overrides for cases, Frank, and STT so Terraform plans can preserve service-specific production hotfix images instead of accidentally pointing every service at `gateway_image_tag`. Production plans should pass or record current live hotfix tags for `cases_image_tag`, `frank_image_tag`, and `stt_image_tag` before apply.


## C4 working-tree patch: reproducible llama model staging

C4 adds a source-controlled model artifact path without local Docker model embedding. Terraform defines a one-shot `llama_model_preload` Fargate task that uses the AWS CLI image, private subnets, the llama-server security group, the llama-server task role, and the Frank EFS access point to download the configured GGUF from private S3 into `/models/llama/`. `scripts/stage_llama_model.py` can optionally upload a local GGUF to the configured private S3 bucket, run the preload task, wait for completion, and enforce an expected SHA256. The running llama-server service continues to mount the model path read-only.
