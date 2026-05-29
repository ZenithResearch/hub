# Production rollout: clean main → image → Terraform apply

This is the standard operator path for updating a live Hub node. It exists to avoid dirty-image deploys, stale tag rollbacks, and ambiguous partial deployments.

## Doctrine

- GitHub `main` is the source of truth, but it is not an automatic deploy trigger.
- Production changes should land on clean `main`, pass CI, then roll through one explicit Terraform plan/apply.
- Container image publication and Terraform deployment are separate steps.
- `scripts/prod_build_image.sh` builds/pushes an immutable image tag; it never updates ECS.
- `scripts/prod_terraform_cd.sh` plans/applies Terraform; it requires explicit image tags for every service so old defaults cannot roll services backward.
- Preserving an unchanged service tag is still part of the full Terraform plan. It is not a direct ECS partial deployment.

## Standard flow

### 1. Confirm clean source on `main`

```bash
git checkout main
git pull --ff-only origin main
git status --short --branch
```

Expected: clean `main` tracking `origin/main`.

If the work is not on `main`, merge or land it first. If the tree is dirty, stop and either commit the intended source or discard/park the dirty work before building a production image.

### 2. Run source verification

Use the focused tests for the change plus the repo checks that apply to the touched surface. For a small process-contract/parser change, for example:

```bash
uv run ruff check services/cases/contract.py tests/test_process_contract.py
uv run pytest tests/test_process_contract.py -q
git diff --check
```

For docs-only changes, verify path/command claims instead of pretending app tests prove the README.

### 3. Push and wait for CI

```bash
git push origin main
gh run list --branch main --limit 5
```

Do not treat a local commit as production-ready until the pushed commit's CI is green or the failure is explicitly classified as unrelated/pre-existing.

### 4. Build and push the production image

Use one immutable tag derived from purpose/date/commit. The default ECR repository is `zenith-hub-prod-gateway-http`, which currently holds the shared app image used by Gateway and Frank.

```bash
export AWS_PROFILE=zenith-hermes
export AWS_REGION=us-east-1
export IMAGE_TAG=clean-main-$(date +%Y%m%d)-$(git rev-parse --short=12 HEAD)
scripts/prod_build_image.sh
```

The script refuses dirty worktrees unless `ALLOW_DIRTY=1` is explicitly set. Do not use dirty builds for normal production updates.

### 5. Inspect live ECS image tags

Before planning, inspect the live service tags. Use the new tag for services intentionally rolling, and preserve current live tags for services not rolling.

```bash
python3 - <<'PY'
import json, subprocess
services = [
    'zenith-hub-prod-gateway-http',
    'zenith-hub-prod-frank',
    'zenith-hub-prod-eventbus',
    'zenith-hub-prod-cases',
    'zenith-hub-prod-stt-http',
]
base = ['aws', '--profile', 'zenith-hermes', '--region', 'us-east-1']
svc = subprocess.check_output(base + ['ecs', 'describe-services', '--cluster', 'zenith-hub-prod-cluster', '--services', *services, '--output', 'json'], text=True)
for service in json.loads(svc)['services']:
    td_arn = service['taskDefinition']
    td = json.loads(subprocess.check_output(base + ['ecs', 'describe-task-definition', '--task-definition', td_arn, '--output', 'json'], text=True))['taskDefinition']
    print(service['serviceName'], td_arn.rsplit('/', 1)[-1])
    for container in td.get('containerDefinitions', []):
        image = container.get('image', '')
        print('  image_tag=', image.rsplit(':', 1)[-1] if ':' in image else image)
PY
```

Never copy image tags from old notes when a live ECS inspection is available.

### 6. Plan with explicit tags

For the common Gateway+Frank shared-image rollout:

```bash
export PROD_TFVARS_PATH=/path/to/local/terraform.tfvars
export TERRAFORM_PLAN_PATH=/tmp/hub-prod-${IMAGE_TAG}.tfplan
export TERRAFORM_PLAN_TEXT=/tmp/hub-prod-${IMAGE_TAG}-plan.txt

export GATEWAY_IMAGE_TAG=$IMAGE_TAG
export FRANK_IMAGE_TAG=$IMAGE_TAG
export EVENTBUS_IMAGE_TAG=<current-live-eventbus-tag>
export CASES_IMAGE_TAG=<current-live-cases-tag>
export STT_IMAGE_TAG=<current-live-stt-tag>

scripts/prod_terraform_cd.sh plan
```

Review the plan before applying. Expected scope for a Gateway+Frank app-image rollout:

- replace Gateway task definition;
- update Gateway service;
- replace Frank task definition;
- update Frank service;
- preserve Eventbus, Cases, and STT HTTP task image tags;
- no unexpected durable RDS/EFS/S3 mutations.

Terraform may show task-definition replacement noise around ECS volume or port mapping normalization. Judge the plan by resource list, image changes, env changes, secrets, and durable infrastructure changes.

### 7. Apply the saved plan

```bash
scripts/prod_terraform_cd.sh apply
```

The helper re-runs plan before apply. If you need to apply an already-reviewed saved plan directly, use Terraform with the saved plan path:

```bash
terraform -chdir=infra/aws_baseline_80 apply -no-color -input=false "$TERRAFORM_PLAN_PATH"
```

### 8. Wait for ECS stability and smoke

```bash
aws --profile zenith-hermes --region us-east-1 ecs wait services-stable \
  --cluster zenith-hub-prod-cluster \
  --services zenith-hub-prod-gateway-http zenith-hub-prod-frank

curl -fsS https://hub.zenith-research.ca/health
```

Then re-run live image inspection and confirm task definitions point at the intended tags.

## STT production baseline

Frank review audio production should normally run:

```text
STT_PROVIDER=elevenlabs
STT_MODEL=scribe_v2
STT_FALLBACK_PROVIDER=local_whisper
STT_AUDIO_PREPROCESSOR=none
ELEVENLABS_API_KEY=<Secrets Manager injected>
```

Keep `stt-http` available for fallback. Do not enable `STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation` globally until side-by-side sample comparisons show it improves quality enough to justify the extra vendor call, latency, cost, and failure mode.

Full STT details: `docs/ops/elevenlabs-stt-rollout.md`.

## Rollback

If smoke fails after a deploy:

1. identify the previous task definition or previous image tag from ECS/GitHub logs;
2. run a new Terraform plan with the previous tag for the affected service(s);
3. apply through Terraform;
4. wait for service stability;
5. run public/operator/internal smokes.

Do not use direct ECS service updates as the normal rollback path unless an operator explicitly chooses an emergency break-glass action.

## Stop conditions

Stop before apply if the plan:

- includes unexpected RDS/EFS/S3 creation, deletion, or replacement;
- removes required secret injection for a provider that remains enabled;
- downgrades STT CPU/memory below the known working production floor;
- rolls an unrelated service to an old image tag;
- exposes a private service such as llama-server publicly;
- was generated from a dirty source tree or an unpushed commit.
