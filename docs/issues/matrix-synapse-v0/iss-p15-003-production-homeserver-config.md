# ISS-P15-003: Production homeserver config

> Issue = PR boundary. Tasks below = commit boundaries inside that PR.

## PR boundary

- **PR scope:** ISS-P15-003: Production homeserver config
- **Suggested branch:** `issue/iss-p15-003-production-homeserver-config`
- **Suggested PR title:** `ISS-P15-003: Production homeserver config`
- **Primary repo:** `ZenithResearch/hub`
- **Supporting repo/API dependency:** none identified for this issue note
- **Source vault note:** `private source note: iss-p15-003`
- **GitHub issue:** https://github.com/ZenithResearch/hub/issues/37
- **Repo-local spec path:** `docs/issues/matrix-synapse-v0/iss-p15-003-production-homeserver-config.md`

## Full spec

### Objective

Point Gateway/ingest/matrix-bridge/Sophia to `synapse.zenith-research.ca` or a documented private equivalent while preserving public server identity.

### Repo rationale

Hub owns Gateway/ingest/appservice readiness, tokens, homeserver config, and smoke tests.

### Dependencies / blocked by

- **P14 dependency satisfied:** accepted production plan/apply, client/federation smoke, capacity, alarm, backup, and isolated restore evidence is recorded in `docs/evidence/matrix-production/iss-p14-007-production.json`.
- P15-004 remains blocked until this issue merges with final configuration evidence.
- P15-005 remains blocked until P15-004 proves production appservice authentication.

### Target files and surfaces

- infra/aws_baseline_80/ecs.tf, service env, docs, .env.example

### Locked decisions and invariants

- Gateway admin endpoint owns v0 Matrix readiness/status/config.
- Production homeserver target is `synapse.zenith-research.ca`.
- Appservice tokens fail closed outside local dev.
- Hub provenance/reply correlation belongs in P18; do not overclaim P15 smoke.

### Acceptance criteria

- Production service config has correct homeserver; public server name remains clear; local/staging/prod differences documented.
- Evidence is recorded in the implementation repo or linked capture before this issue is marked complete.
- The project note is updated with the completion evidence and any downstream blockers.

### Verification commands

- terraform plan; docker compose config for local; pytest config tests.

### Forbidden claims / non-goals

- Do not claim production deployment unless live deploy evidence exists.
- Do not claim Matrix identity is Hub authority.
- Do not print or persist raw appservice/admin/reviewer secrets.
- Do not claim wallet, secS-magik, Zenith Review SDK wallet-auth, or Dregg-backed authorization in this v0 Matrix/Synapse issue set.

## Task list — commit boundaries

Each checked task should land as a separate commit on the PR branch. Do not combine tasks unless the diff is mechanically inseparable; if combined, explain why in the PR body.

### Task 1: Scope and baseline evidence

**Commit boundary:** one commit in the `ISS-P15-003` PR.

**Objective:** Read the source vault note and inspect the target repo surfaces for `ISS-P15-003`. Confirm the exact files/modules to touch, record current behavior, and update this spec if discovery changes the file list.

**Files / surfaces:**
- infra/aws_baseline_80/ecs.tf, service env, docs, .env.example

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "docs: scope iss-p15-003"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 2: Contract / failing test or guard

**Commit boundary:** one commit in the `ISS-P15-003` PR.

**Objective:** Add the smallest failing test, static check, fixture, or documentation guard that proves the issue is not already complete and captures the desired behavior before implementation.

**Files / surfaces:**
- infra/aws_baseline_80/ecs.tf, service env, docs, .env.example

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "test: cover iss-p15-003 contract"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 3: Implement the primary behavior

**Commit boundary:** one commit in the `ISS-P15-003` PR.

**Objective:** Make the minimal production change for the objective. Keep the diff limited to this issue's PR boundary and do not pull in adjacent phase work.

**Files / surfaces:**
- infra/aws_baseline_80/ecs.tf, service env, docs, .env.example

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "feat: implement iss-p15-003"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 4: Negative cases and edge behavior

**Commit boundary:** one commit in the `ISS-P15-003` PR.

**Objective:** Add fail-closed, non-leakage, duplicate/idempotency, unavailable-dependency, or no-op cases relevant to this issue. If the issue is documentation-only, add explicit forbidden examples instead.

**Files / surfaces:**
- infra/aws_baseline_80/ecs.tf, service env, docs, .env.example

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "test: harden iss-p15-003 edge cases"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 5: Docs, operator notes, and evidence hooks

**Commit boundary:** one commit in the `ISS-P15-003` PR.

**Objective:** Update repo-local docs/runbooks/config comments so an operator or future agent can verify the behavior without reading the vault. Add evidence placeholders or command examples, but do not commit secrets or live tokens.

**Files / surfaces:**
- infra/aws_baseline_80/ecs.tf, service env, docs, .env.example

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "docs: record iss-p15-003 operator evidence"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 6: PR readiness verification

**Commit boundary:** one commit in the `ISS-P15-003` PR.

**Objective:** Run the verification commands below, run `git diff --check`, inspect the PR diff for scope creep/secrets, and update the PR body with evidence and explicit non-claims.

**Files / surfaces:**
- infra/aws_baseline_80/ecs.tf, service env, docs, .env.example

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "chore: verify iss-p15-003 pr readiness"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.

## PR body checklist

Before opening or marking the PR ready, include:

- [ ] Link to this repo-local spec.
- [ ] Link to source vault note `private source note: iss-p15-003`.
- [ ] Summary of the implementation.
- [ ] Task/commit list with commit SHAs.
- [ ] Verification commands and results.
- [ ] Explicit forbidden claims that remain false.
- [ ] Supporting repo/API dependency status, if any.

## Related

- [[prp-pr-015|PRP-PR-015: Production Synapse smoke and Hub appservice wiring]]
- [[../capture/2026-06-04-matrix-wallet-extension-initiative|Matrix production and vanilla auth initiative]]
- [[projects]]
- [[Zenith]]

Areas:
- [[Zenith]]
- [[projects]]
