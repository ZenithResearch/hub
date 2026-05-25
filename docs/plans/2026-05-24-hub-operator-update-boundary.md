# Hub Operator Update Boundary Implementation Plan

> **Status:** Historical plan. The operator-update boundary now lives in `docs/operations/operator-updates.md`; the repository-owned automaton contract and fixed Mermaid diagram live in `docs/operations/review-case-automaton.md`.
>
> **For Hermes:** Use subagent-driven-development skill to implement future plan task-by-task after boundary decisions are accepted.

**Goal:** Make Hub updates explicit, operator-controlled, and fork/community-safe instead of treating GitHub `main` as an automatic deployment trigger for Gabriel's live Hub.

**Architecture:** GitHub `main` is the canonical source tree, not a deployment command. Each running Hub node records the source ref/image tags/config profile it has chosen, and updates happen through an explicit plan/apply script. GitHub Actions may remain as one operator backend, but production deploy is manual, scoped, and profile-specific.

**Tech Stack:** Bash/Python operator scripts, Docker Compose for local/self-hosted profiles, Terraform/GitHub Actions for cloud-prod profile, JSON deployment manifests, existing Hub smoke scripts.

---

## Reassessment

PR #3 is merged into `origin/main` at `ce56d0c`.

That changes the source-of-truth situation but not the live-runtime situation:

- `main` now contains the Review Case Automaton fix.
- The live Hub should not be assumed to update automatically from `main`.
- The previous GitHub Actions Production CD path is manual already, but it currently presents as “production CD from GitHub” rather than a portable operator update contract.
- Community/fork safety argues against continuous deployment from the public repo to Gabriel's live node.
- The current production stuck-case symptom is still an execution/assignment-plane problem before Step 1, not an automaton Step 8 result.

## Decision

Use this deployment doctrine:

1. **GitHub main = public/community canonical source.**
2. **Each Hub node = operator-owned deployment.**
3. **Updates = explicit plan/apply, never implicit merge-to-prod.**
4. **GitHub Actions CD = optional cloud-prod operator backend, not the default community path.**
5. **Running nodes record their deployed ref/image/profile locally without secrets.**

## Next three steps

### Step 1: Define the operator update contract

**Objective:** Add a repo-owned plan and documentation boundary that explains how a Hub node chooses and records a deployed version.

**Files:**
- Create: `docs/plans/2026-05-24-hub-operator-update-boundary.md`
- Create: `docs/operations/operator-updates.md`
- Create: `deployments/operator-state.example.json`

**Acceptance:**
- The docs clearly distinguish source, release, image, config profile, and running node state.
- The docs explicitly say merge-to-main does not auto-deploy Gabriel's live Hub.
- The example operator-state file contains no secrets and records only refs/tags/profile/timestamps.

**Stop condition:** Stop before implementing scripts if profile behavior is still ambiguous.

### Step 2: Implement a dry-run update planner

**Objective:** Add a no-side-effect command that tells an operator what would change before any deploy/restart.

**Files:**
- Create: `scripts/hub_update.py`
- Modify: `README.md` or `docs/operations/operator-updates.md`
- Test: `tests/test_hub_update.py`

**CLI shape:**

```bash
python scripts/hub_update.py plan \
  --ref ce56d0c \
  --profile local-dev \
  --state deployments/operator-state.json
```

**Required behavior:**
- Reads current operator-state JSON if present.
- Resolves target git ref locally.
- Prints current ref vs target ref.
- Lists affected update domains: source checkout, image build/pull, migrations, service restart, smoke.
- Does not modify files, run Terraform, restart services, or print secrets.

**Acceptance:**
- Unit tests cover missing state, existing state, invalid profile, invalid ref, and plan output shape.
- Plan mode exits nonzero for unknown profile/ref.

**Stop condition:** Do not add `apply` until plan output is stable and reviewed.

### Step 3: Add guarded apply adapters by profile

**Objective:** Add explicit update execution for local/self-hosted first, then cloud-prod only after backend permissions are fixed.

**Files:**
- Modify: `scripts/hub_update.py`
- Create or modify tests under `tests/test_hub_update.py`
- Possibly create: `docs/operations/cloud-prod-update.md`

**Profiles:**

1. `local-dev`
   - Fetch/ref checkout confirmation.
   - Optional Docker Compose rebuild/restart.
   - Local health checks.

2. `self-hosted-single-node`
   - Similar to local-dev but assumes durable volumes and stricter backup prompt.

3. `cloud-prod`
   - Does not auto-run by default.
   - Emits exact GitHub Actions/Terraform command or invokes workflow only with `--confirm`.
   - Requires explicit service image tags.
   - Requires Terraform backend access check before plan/apply.

**Acceptance:**
- `apply` always requires explicit confirmation.
- `cloud-prod apply` refuses if Terraform backend state access is unavailable.
- `apply` writes updated operator-state only after smoke passes.

**Stop condition:** If cloud-prod state access remains blocked by S3 403, leave cloud-prod apply disabled and document the IAM fix.

## Non-goals

- No automatic deploy from `main` to Gabriel's live Hub.
- No GitHub Actions scheduled/continuous production deployment.
- No dirty-tree commits from an operator's local Hub working tree.
- No secret-bearing deployment manifest in git.
- No attempt to resolve the production waiting-assignment bug in this checkpoint.

## Immediate live-Hub posture

This section is superseded by the operator-controlled rollout doctrine in `docs/operations/operator-updates.md`. At the time of this plan, `ce56d0c` had been merged but was not yet deployed. That first-slice automaton deployment was later completed through a local operator-controlled Terraform apply.

For new source changes, do not infer production state from merge state. Land the source change, build immutable image tags, run a local operator Terraform plan preserving unaffected service tags, review scope, apply explicitly, then verify ECS stability and smoke results.

## Verification commands

For Step 1 docs-only checkpoint:

```bash
git status --short
git diff --check docs/plans/2026-05-24-hub-operator-update-boundary.md docs/operations/operator-updates.md deployments/operator-state.example.json
```

For Step 2 planner checkpoint:

```bash
python -m unittest tests.test_hub_update -q
python scripts/hub_update.py plan --ref HEAD --profile local-dev --state deployments/operator-state.example.json
```

For Step 3 apply checkpoint:

```bash
python -m unittest tests.test_hub_update -q
python scripts/hub_update.py apply --ref HEAD --profile local-dev --state /tmp/hub-operator-state.json --dry-run
```
