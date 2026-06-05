# ISS-P14-004: Secret management boundary

> Issue = PR boundary. Tasks below = commit boundaries inside that PR.

## PR boundary

- **PR scope:** ISS-P14-004: Secret management boundary
- **Suggested branch:** `issue/iss-p14-004-secret-management-boundary`
- **Suggested PR title:** `ISS-P14-004: Secret management boundary`
- **Primary repo:** `ZenithResearch/hub`
- **Supporting repo/API dependency:** none identified for this issue note
- **Source vault note:** `private source note: iss-p14-004`
- **GitHub issue:** https://github.com/ZenithResearch/hub/issues/32
- **Repo-local spec path:** `docs/issues/matrix-synapse-v0/iss-p14-004-secret-management-boundary.md`

## Full spec

### Objective

Move Synapse and appservice secrets away from raw committed material into Secrets Manager/SSM/operator-secret references.

### Repo rationale

Hub owns production Terraform, DNS/TLS, Matrix secrets, backup, and deployment-path tests.

### Dependencies / blocked by

- ISS-P14-002

### Target files and surfaces

- infra/aws_baseline_80/secrets.tf, variables.tf, matrix user-data/template, .env examples

### Locked decisions and invariants

- V0 production Synapse runtime is EC2 + encrypted EBS.
- Pricing evidence is required before production apply.
- `synapse.zenith-research.ca` is direct server/client host.
- Federation `8448` is intentionally enabled for v0.
- TLS termination path must be chosen with tradeoff evidence before implementation.

### Acceptance criteria

- Required secret classes are named; committed examples use placeholders; Terraform uses secret references or sensitive vars; rotation owner is documented.
- Evidence is recorded in the implementation repo or linked capture before this issue is marked complete.
- The project note is updated with the completion evidence and any downstream blockers.

### Verification commands

- private artifact scan; git grep secret/token patterns; terraform validate/plan.

### Forbidden claims / non-goals

- Do not claim production deployment unless live deploy evidence exists.
- Do not claim Matrix identity is Hub authority.
- Do not print or persist raw appservice/admin/reviewer secrets.
- Do not claim wallet, secS-magik, Zenith Review SDK wallet-auth, or Dregg-backed authorization in this v0 Matrix/Synapse issue set.

## Task list — commit boundaries

Each checked task should land as a separate commit on the PR branch. Do not combine tasks unless the diff is mechanically inseparable; if combined, explain why in the PR body.

### Task 1: Scope and baseline evidence

**Commit boundary:** one commit in the `ISS-P14-004` PR.

**Objective:** Read the source vault note and inspect the target repo surfaces for `ISS-P14-004`. Confirm the exact files/modules to touch, record current behavior, and update this spec if discovery changes the file list.

**Files / surfaces:**
- infra/aws_baseline_80/secrets.tf, variables.tf, matrix user-data/template, .env examples

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "docs: scope iss-p14-004"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 2: Contract / failing test or guard

**Commit boundary:** one commit in the `ISS-P14-004` PR.

**Objective:** Add the smallest failing test, static check, fixture, or documentation guard that proves the issue is not already complete and captures the desired behavior before implementation.

**Files / surfaces:**
- infra/aws_baseline_80/secrets.tf, variables.tf, matrix user-data/template, .env examples

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "test: cover iss-p14-004 contract"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 3: Implement the primary behavior

**Commit boundary:** one commit in the `ISS-P14-004` PR.

**Objective:** Make the minimal production change for the objective. Keep the diff limited to this issue's PR boundary and do not pull in adjacent phase work.

**Files / surfaces:**
- infra/aws_baseline_80/secrets.tf, variables.tf, matrix user-data/template, .env examples

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "feat: implement iss-p14-004"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 4: Negative cases and edge behavior

**Commit boundary:** one commit in the `ISS-P14-004` PR.

**Objective:** Add fail-closed, non-leakage, duplicate/idempotency, unavailable-dependency, or no-op cases relevant to this issue. If the issue is documentation-only, add explicit forbidden examples instead.

**Files / surfaces:**
- infra/aws_baseline_80/secrets.tf, variables.tf, matrix user-data/template, .env examples

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "test: harden iss-p14-004 edge cases"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 5: Docs, operator notes, and evidence hooks

**Commit boundary:** one commit in the `ISS-P14-004` PR.

**Objective:** Update repo-local docs/runbooks/config comments so an operator or future agent can verify the behavior without reading the vault. Add evidence placeholders or command examples, but do not commit secrets or live tokens.

**Files / surfaces:**
- infra/aws_baseline_80/secrets.tf, variables.tf, matrix user-data/template, .env examples

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "docs: record iss-p14-004 operator evidence"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 6: PR readiness verification

**Commit boundary:** one commit in the `ISS-P14-004` PR.

**Objective:** Run the verification commands below, run `git diff --check`, inspect the PR diff for scope creep/secrets, and update the PR body with evidence and explicit non-claims.

**Files / surfaces:**
- infra/aws_baseline_80/secrets.tf, variables.tf, matrix user-data/template, .env examples

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "chore: verify iss-p14-004 pr readiness"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.

## PR body checklist

Before opening or marking the PR ready, include:

- [ ] Link to this repo-local spec.
- [ ] Link to source vault note `private source note: iss-p14-004`.
- [ ] Summary of the implementation.
- [ ] Task/commit list with commit SHAs.
- [ ] Verification commands and results.
- [ ] Explicit forbidden claims that remain false.
- [ ] Supporting repo/API dependency status, if any.

## Related

- [[prp-pr-014|PRP-PR-014: Production Synapse core Terraform]]
- [[../capture/2026-06-04-matrix-wallet-extension-initiative|Matrix production and vanilla auth initiative]]
- [[projects]]
- [[Zenith]]

Areas:
- [[Zenith]]
- [[projects]]

## PR readiness evidence — 2026-06-05

- Issue PR branch: `issue/iss-p14-004-secret-management-boundary-main`
- Targeted test: `uv run --with pytest pytest tests/matrix/test_iss_p14_004_secret_boundary.py -q`
- Scope: this PR completes ISS-P14-004: Secret management boundary only; it does not claim production deployment, appservice delivery, or Matrix identity as Hub authority.
- Review boundary: issue = PR, with test-first contract commit followed by implementation and readiness evidence.
