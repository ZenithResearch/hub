# Deployment profiles

Project G makes Hub deployment targets explicit so local Docker, self-hosted/on-prem, staging, and production are not treated as one blurry environment.

Canonical machine-readable contract:

- `infra/deployment-profiles.yaml`

Validation:

```bash
python3 scripts/deployment_profile_check.py
```

## Profiles

### local-dev

Purpose: disposable local development and demos.

Source of truth:

- code/config: git checkout + local `.env`;
- review auth, cases, artifacts, Matrix: local volumes/data only;
- model artifacts: local providers/host files, not durable source of truth.

Rules:

- Local Docker data is disposable only after the user accepts local-data loss or exports/moves aside wanted volumes.
- `.env`, `.hermes`, generated Matrix registrations, DB files, and review artifacts stay untracked.
- Matrix is optional and must follow the generated `/data/appservices` contract.

Smoke:

```bash
scripts/ci_check.sh
python3 scripts/matrix_deployment_check.py
./scripts/start.sh
curl -fsS "http://localhost:${HTTP_PORT:-8080}/health"
```

### self-hosted-single-node

Purpose: an operator/fork-owned host, VM, or small on-prem node.

Source of truth:

- code/config: git tag/commit + host env/secrets;
- review auth/cases/artifacts: host volumes or chosen managed DB/object store;
- Matrix: host Synapse DB/media/signing key backups;
- model artifacts: host model directory or private object store, not app image layers.

Rules:

- Public ingress terminates only to `gateway-http`.
- Internal services remain private to the host/network.
- Backups and restore drills are required before the node is called durable.
- Matrix can be enabled, but `DEPLOYMENT_PARITY.md` applies.

Smoke:

```bash
python3 scripts/matrix_deployment_check.py
docker compose config --quiet
curl -fsS "https://<host>/health"
```

### cloud-aws-staging

Purpose: AWS rehearsal environment for deploys, review SDK previews, Matrix topology, and destructive testing before prod.

Source of truth:

- code/config: git branch/commit + staging Terraform state;
- data: staging RDS/EFS/S3 or explicitly disposable stores;
- model artifacts: private staging S3 -> one-shot preload -> EFS, or smaller external model.

Rules:

- Staging must be visibly not prod.
- Staging can be reset if its reset policy says so.
- CD should be manual first, then automated after it becomes boring.

Smoke:

```bash
scripts/ci_check.sh
terraform -chdir=infra/aws_baseline_80 validate -no-color
# plus staging equivalent of scripts/prod_smoke.py
```

### cloud-aws-prod

Purpose: authoritative production Hub runtime and durable live data.

Source of truth:

- code/config: reviewed commits + Terraform state;
- review auth: production RDS clients registry;
- cases/artifacts: production storage;
- model artifacts: private S3 bucket + staged EFS copy;
- Matrix: not adopted into prod baseline yet; once enabled, its DB/media/signing key must be backed up.

Rules:

- No local Docker dependency.
- No long-lived GitHub AWS keys.
- Terraform plan/apply only through reviewed manual CD path or explicit local operator equivalent.
- Production smoke must pass before and after apply.
- Local Docker reset remains gated on prod/source-of-truth proof and user acceptance.

Smoke:

```bash
python3 scripts/prod_smoke.py --target prod --mode public
python3 scripts/prod_smoke.py --target prod --mode operator
python3 scripts/prod_smoke.py --target prod --mode internal --run-internal-probes
scripts/prod_terraform_cd.sh plan
```

## Why this exists

The prior failure mode was treating “the Hub deployment” as one environment. That blurred together:

- local Docker state;
- production AWS state;
- self-hosted/on-prem requirements;
- Matrix/Synapse statefulness;
- model artifact lifecycle;
- CI/CD deploy authority.

This profile split forces future work to say which environment owns which data and which smoke test proves readiness.
