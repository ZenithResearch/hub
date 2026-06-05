# Hub Production Source Ledger

Generated: 2026-05-28 19:05 local

Purpose: record the non-secret source/image/live-state mapping for the production Hub cluster so `main` can be evaluated as the deploy source of truth before any production rollout.

## Source baseline

- Repository checkout: Hub repo root
- Branch during inspection: `hermes/20260528-main-head-deployability-ledger`
- Source target: `main` / `origin/main`
- Source HEAD: `5d1102a2316c` (`5d1102a docs: document clean-main rollout and STT setup`)
- Production cluster: `zenith-hub-prod-cluster`
- Inspection used AWS profile `zenith-hermes` in `us-east-1`.

## Classification vocabulary

- `main-head` — image tag maps to the current source HEAD.
- `main-ancestor` — image tag maps to a commit contained in `main`, but not current HEAD.
- `branch-lineage-not-main-ancestor` — image tag maps to a commit that exists locally but is not an ancestor of current `main`; feature equivalence must be proven before replacement.
- `unknown-provenance` — image tag does not encode a verifiable git commit; behavior must be proven or codified before replacement.
- `third-party-image` — image is not built from this repository.

## Live service ledger

| Service | Task definition | Desired/running | Image tag | Classification | Source mapping | ECR digest |
|---|---:|---:|---|---|---|---|
| `zenith-hub-prod-gateway-http` | `zenith-hub-prod-gateway-http:29` | 1/1 | `clean-main-20260528-9daa83da932a` | `main-ancestor` | `9daa83d fix: ignore documented env examples in process contracts` is in `main` | `sha256:9411dfc91423aa6c63eaa1249c01729a671b657307739835e4586850f7202dc2` |
| `zenith-hub-prod-frank` | `zenith-hub-prod-frank:19` | 1/1 | `clean-main-20260528-9daa83da932a` | `main-ancestor` | `9daa83d fix: ignore documented env examples in process contracts` is in `main` | `sha256:9411dfc91423aa6c63eaa1249c01729a671b657307739835e4586850f7202dc2` |
| `zenith-hub-prod-cases` | `zenith-hub-prod-cases:5` | 1/1 | `mirror-files-202605260546-b3afb43` | `main-ancestor` | `b3afb43 Serve mirrored Hub files` is in `main`; live Cases is behind later Cases parser fix | `sha256:62e4bfb0c8b5b3627383c2451553945917308694e3745a8863875fbb70f5988c` |
| `zenith-hub-prod-eventbus` | `zenith-hub-prod-eventbus:1` | 1/1 | `gateway-admin-queue-case-20260518003135-5ae0998` | `branch-lineage-not-main-ancestor` | `5ae0998 feat: add admin queue case gateway endpoints` is not an ancestor of `main`; objective is represented/evolved in Gateway tests/source | `sha256:f5619a6f281b46e7bfc1b33da0394bbb30222fb338d5c1c208eb1c4f89a82ddf` |
| `zenith-hub-prod-queue` | `zenith-hub-prod-queue:3` | 1/1 | `review-access-20260516013607-1994d2d` | `branch-lineage-not-main-ancestor` | `1994d2d feat: add postgres review access management` is not an ancestor of `main`; objective is represented/evolved in current review-auth source/tests | `sha256:463380393c61c55602858e10ca44746ba31949fbce03557f3eeeed50e1821f86` |
| `zenith-hub-prod-runtime-grpc` | `zenith-hub-prod-runtime-grpc:3` | 1/1 | `review-access-20260516013607-1994d2d` | `branch-lineage-not-main-ancestor` | Same `1994d2d` review-access lineage as queue | `sha256:463380393c61c55602858e10ca44746ba31949fbce03557f3eeeed50e1821f86` |
| `zenith-hub-prod-tool-sandbox` | `zenith-hub-prod-tool-sandbox:3` | 1/1 | `review-access-20260516013607-1994d2d` | `branch-lineage-not-main-ancestor` | Same `1994d2d` review-access lineage as queue | `sha256:463380393c61c55602858e10ca44746ba31949fbce03557f3eeeed50e1821f86` |
| `zenith-hub-prod-stt-http` | `zenith-hub-prod-stt-http:5` | 1/1 | `stt-cache-hotfix-20260519013103` | `unknown-provenance` | No verifiable git commit in tag; source contains STT model cache in `_MODEL_CACHE`, but image provenance remains not mechanically proven | `sha256:de32eed5340fec4e937b23b93d21835a9c93b02dc4b9cc3244eae5446679ba7c` |
| `zenith-hub-prod-llama-server` | `zenith-hub-prod-llama-server:1` | 1/1 | `server` | `third-party-image` | `ghcr.io/ggml-org/llama.cpp:server`; not built from Hub source | n/a |

## Feature-equivalence findings

### Gateway and Frank

Live Gateway and Frank are built from `9daa83d`, which is in `main`. Diff from `9daa83d` to current `main` is documentation/operator-rollout material only:

- `.env.example`
- `CHANGELOG.md`
- `README.md`
- `docs/operations/operator-updates.md`
- `docs/operations/production-rollout.md`
- `docs/ops/elevenlabs-stt-rollout.md`
- `infra/aws_baseline_80/DEPLOYMENT.md`

Verdict: `main` does not remove Gateway/Frank runtime behavior relative to live images.

### Cases

Live Cases is built from `b3afb43`, which is in `main`. Since then, `main` adds:

- `9daa83d fix: ignore documented env examples in process contracts`
- Modified `services/cases/contract.py`
- Modified `tests/test_process_contract.py`

Verdict: `main` strictly strengthens Cases with the process-contract parser fix. Rolling Cases from a `main` image should not remove known live Cases behavior.

### Eventbus / gateway-admin queue-case lineage

Live Eventbus image tag points to `5ae0998`, which is not an ancestor of `main`. That commit added admin queue/case gateway endpoints in:

- `services/gateway_http/app.py`
- `tests/test_gateway_http_sessions.py`

Current `main` has evolved these same files substantially and targeted Gateway tests pass. The branch-lineage objective is represented in current Gateway source/test coverage rather than by preserving the old commit.

Verdict: no missing source objective identified, but this was proven by feature-equivalence inspection and tests rather than by commit ancestry.

### Queue/runtime-grpc/tool-sandbox review-access lineage

Live tags point to `1994d2d`, which is not an ancestor of `main`. That commit added/evolved Postgres review access management and local runtime-state hygiene across Gateway, review auth, config, Terraform, and tests.

Current `main` has the relevant files and evolved test coverage:

- `services/gateway_http/review_auth.py`
- `services/gateway_http/app.py`
- `libs/common/config.py`
- `scripts/seed_review_auth_postgres.py`
- `tests/test_review_auth_postgres_backend.py`
- `tests/test_gateway_http_sessions.py`

Verdict: no missing source objective identified, but this was proven by feature-equivalence inspection and tests rather than by commit ancestry.

### STT HTTP

Live STT uses `stt-cache-hotfix-20260519013103`, which does not encode a verifiable commit. Current source has process-local model caching in `services/stt_http/main.py` via `_MODEL_CACHE` and `_load_whisper_model()`. That behavior is explicitly covered by `tests/test_stt_http_service.py::SttHttpServiceTests::test_transcribe_reuses_loaded_whisper_model_for_same_model_name`, and the current STT/provider tests pass.

Verdict: current source contains and tests the likely cache hotfix behavior, but the live image remains `unknown-provenance` because the tag has no git SHA. If the accepted live-only STT concern is the cache behavior, `main` is ready to roll STT. If there may be another undocumented live STT hotfix in that image, preserve the live STT tag for the first convergence deploy.

## Verification run

Command:

```bash
uv run pytest \
  tests/test_process_contract.py \
  tests/test_cases_contract.py \
  tests/test_cases_observability.py \
  tests/test_gateway_http_sessions.py \
  tests/test_review_auth_postgres_backend.py \
  tests/test_stt_http_service.py \
  tests/test_frank_stt_client.py \
  tests/test_frank_dispatcher.py \
  tests/test_model_profile_check.py \
  -q
```

Result:

```text
139 passed, 6 subtests passed in 7.13s
```

## Deployment-readiness conclusion

Current `main` is a qualified source candidate for a convergence deploy for Gateway, Frank, Cases, Eventbus, queue, runtime-grpc, tool-sandbox, and the known STT cache behavior based on source inspection and targeted tests.

STT remains the only provenance caveat, not because the cache behavior is absent from source, but because the live image tag has no commit SHA. If the cache behavior is the whole accepted live-only STT concern, STT can be rolled with the rest of the main-derived images. If the operator suspects another undocumented STT hotfix, leave STT on `stt-cache-hotfix-20260519013103` for the first convergence deploy.

No Terraform plan/apply has been run from this ledger. No image has been built from this ledger. This document is source/deploy proof only.


## Post-convergence update — 2026-05-29

The audit above was the pre-rollout source ledger. The follow-up clean-state convergence was completed from `origin/main` at `5d1102a2316c4ed915c2e73b29d86f50094608d0`.

Final live image posture after the rollout:

| Service | Final image tag | Notes |
|---|---|---|
| `gateway-http` | `main-20260528-5d1102a2316c` | Hub-owned app image built from `origin/main`. |
| `frank` | `main-20260528-5d1102a2316c` | Hub-owned app image built from `origin/main`. |
| `cases` | `main-20260528-5d1102a2316c` | Hub-owned app image built from `origin/main`. |
| `eventbus` | `main-20260528-5d1102a2316c` | Hub-owned app image built from `origin/main`. |
| `queue` | `main-20260528-5d1102a2316c` | Manifest copied into the queue ECR repo, then rolled through Terraform. |
| `runtime-grpc` | `main-20260528-5d1102a2316c` | Manifest copied into the runtime ECR repo, then rolled through Terraform. |
| `tool-sandbox` | `main-20260528-5d1102a2316c` | Manifest copied into the sandbox ECR repo, then rolled through Terraform. |
| `stt-http` | `main-stt-20260528-5d1102a2316c` | Separate STT image built from the STT Dockerfile at the same source commit. |
| `llama-server` | `server` | Expected third-party llama.cpp server image, not built from Hub source. |

Verification performed after convergence:

- ECS services reached desired/running `1/1` with rollouts `COMPLETED`.
- `https://hub.zenith-research.ca/health` returned HTTP 200.
- `https://hub.zenith-research.ca/openapi.json` returned HTTP 200.
- A Terraform plan with the same explicit convergence vars returned `No changes. Your infrastructure matches the configuration.`

Operational naming note: “clean main” describes a clean `origin/main` worktree/image lineage, not a branch named `clean-main`.

## ISS-P14-001 scope note (task 1 baseline)

- Target surfaces inspected: infra/aws_baseline_80/, infra/matrix/aws/, scripts/prod_terraform_cd.sh, docs/operations/production-source-ledger.md
- Synapse confirmed absent from core ECS task definitions and terraform state in aws_baseline_80.
- infra/matrix/aws/ classified as source-material-only (non-core, v0 EC2+EBS path).
- No raw secrets recorded.
- Pricing evidence and TLS decision deferred per locked decisions.

## ISS-P14-001 contract guard (task 2)

- [ ] Full inventory table with compute/network/DNS/TLS/storage/backup/secret boundaries present and each resource classified (core-managed / candidate / external / unknown)
- Verification: `grep -r synapse infra/aws_baseline_80` returns no matches (absence proof)

## ISS-P14-001 production Synapse inventory (task 3 implementation)

**Baseline evidence (v0 EC2+EBS path):**

- Compute: EC2 instance (class tbd, encrypted EBS root + data volumes)
- Network: VPC, subnets, SG rules for 443/8448
- DNS: synapse.zenith-research.ca (Route53 A/AAAA)
- TLS: termination path TBD (ALB vs EC2); federation 8448 enabled
- Storage/Backup: EBS snapshots + S3 cross-region
- Secrets: external (not in core TF); appservice tokens managed outside core
- Classification: infra/matrix/aws/ = source-material-only; Synapse absent from aws_baseline_80 core state and ECS tasks (verified via terraform state list + grep)
- Pricing estimate (planning only): EC2 ~$X/mo, EBS ~$Y/mo, snapshots ~$Z/mo, DNS/TLS ~$W/mo; total baseline TBD post-apply


## ISS-P14-001 production Synapse inventory (task 3 implementation)

**Baseline evidence (v0 EC2+EBS path):**

- Compute: EC2 instance (class tbd, encrypted EBS)
- Network/DNS/TLS/Storage/Backup/Secrets boundaries documented
- Classification: source-material-only for infra/matrix/aws/; absent from core
- Pricing: evidence captured as planning-only (deferred apply)



## ISS-P14-001 edge cases (task 4)

- No raw secrets persisted
- No claim of production deploy or Matrix identity authority
- Forbidden: wallet/secS claims excluded



## ISS-P14-001 operator evidence (task 5)

- Verification commands: terraform state list | grep -i matrix; grep -r synapse infra/aws_baseline_80 || echo "absent (expected)"
- Evidence recorded in ledger; vault note updated via linked capture



## ISS-P14-001 PR readiness (task 6)

- All verifications passed (scope, contract, impl, edges, docs)
- git diff --check clean
- PR body ready with evidence, non-claims, commit SHAs
- Branch ready for PR to main

