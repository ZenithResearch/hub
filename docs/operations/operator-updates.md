# Hub Operator Updates

Hub is source-available/community-facing infrastructure, but a running Hub node is operator-owned state. A merge to GitHub `main` updates the canonical source tree; it does not automatically update any particular node.

## Deployment doctrine

- `main` is source, not a deploy command.
- Release tags are recommended stable update points.
- Operators choose when their node moves to a ref or image tag.
- Production updates are explicit `plan` then `apply` actions.
- GitHub Actions builds images and runs CI; production Terraform deploys are local/operator-controlled, not GitHub CD.
- Secrets and real runtime state stay outside git.

## Terms

- **source ref**: a git commit, branch, or tag such as `ce56d0c` or `v0.3.1`.
- **image tag**: a container image tag built from a source ref.
- **profile**: the runtime environment shape, such as `local-dev`, `self-hosted-single-node`, or `cloud-prod`.
- **operator state**: local non-secret record of what a node believes it is running.
- **plan**: a no-side-effect report of what would change.
- **apply**: an explicit action that updates source/images/services and writes operator state only after smoke passes.

## Supported profiles

### local-dev

For disposable local development stacks. Updates may rebuild containers and restart services, but should not assume durable production data.

### self-hosted-single-node

For an operator running one durable Hub node. Updates must warn before migrations/restarts and should recommend backup snapshots before apply.

### cloud-prod

For Gabriel's current AWS production-style Hub and future cloud nodes. Updates must preserve service-specific image tags for unaffected services, run Terraform plan before apply, and refuse apply if backend state cannot be read.

## Operator state

Use `deployments/operator-state.example.json` as the public shape. Real state files should be local/private and may live outside the repo.

Do not store secrets, tokens, tfvars, database paths with private data, or raw credentials in operator state.

## Review Case Automaton contract

The repository-owned Review Case Automaton contract is documented in `docs/operations/review-case-automaton.md` and cross-linked from `docs/frank-native-case-pipeline.md` and `docs/gateway-http.md`.

Its current scope is deliberately narrow: ready packets succeed, non-ready packets fail terminally, and Frank retry/rerun/fix-loop semantics are not part of the review or terminal-state automaton. The 2026-05-24 planning notes are historical; the operations document is the current source contract.

The previous first-slice automaton source was merged into GitHub main at `ce56d0c` and explicitly deployed to the live Hub by local operator-controlled Terraform apply. The live Gateway and Frank services were verified then on image tag `review-case-automaton-202605250235-a918d6d`; public, operator, and internal smoke checks passed after that rollout. New source changes still require the explicit plan/apply flow below before they are production facts.

The previous GitHub Actions Production CD workflow has been removed from the repository because production deploys are intentionally local/operator-controlled. Its historical failures should not be read as product or deployment-health failures. Local operator deploy remains the proven production update path.

## Safe update flow

1. Choose a target ref or release tag.
2. Run update plan.
3. Review changed domains: source, images, migrations, services, smoke.
4. For cloud-prod, verify Terraform backend access before plan/apply.
5. Export the current live image tags for every service that is not intentionally rolling; the local Terraform helper intentionally has no stale production image-tag defaults.
6. Apply only with explicit confirmation.
7. Run smoke checks.
8. Write operator state after smoke passes.

## Dry-run planner

Use the dry-run planner before any update:

```bash
python scripts/hub_update.py plan \
  --repo-dir . \
  --ref HEAD \
  --profile local-dev \
  --state deployments/operator-state.json
```

Plan mode is intentionally side-effect free. It does not checkout refs, write state, run Terraform, restart services, or print secrets.

Supported profiles:

- `local-dev`
- `self-hosted-single-node`
- `cloud-prod`

The planner exits nonzero for unknown profiles, invalid refs, invalid JSON state, or non-git directories.

## Guarded apply scaffold

Apply mode exists only as a guarded scaffold right now:

```bash
python scripts/hub_update.py apply \
  --dry-run \
  --repo-dir . \
  --ref HEAD \
  --profile local-dev \
  --state deployments/operator-state.json
```

Rules:

- non-dry-run apply requires `--confirm`;
- dry-run apply has no side effects and wraps the same plan payload;
- `cloud-prod apply` is disabled until Terraform backend access checks exist and pass;
- future real apply adapters must run smoke checks before writing operator state.

## Local production Terraform helper

Use `scripts/prod_terraform_cd.sh plan` / `apply` only from an authenticated operator shell after inspecting live ECS image tags. The helper requires explicit image-tag environment variables for every service:

```bash
export GATEWAY_IMAGE_TAG=<intended gateway-http tag>
export EVENTBUS_IMAGE_TAG=<current live eventbus tag unless rolling eventbus>
export CASES_IMAGE_TAG=<current live cases tag unless rolling cases>
export FRANK_IMAGE_TAG=<intended/current Frank tag>
export STT_IMAGE_TAG=<current live STT tag unless rolling STT>
scripts/prod_terraform_cd.sh plan
```

The absence of default production tags is intentional: stale defaults can silently roll a service backward after hotfixes. Preserve unaffected services from live ECS inspection rather than from memory or old docs.

## Non-goals

- No automatic deploy from public `main` to Gabriel's live Hub.
- No default continuous deployment for community operators.
- No git-tracked secret-bearing deployment state.
