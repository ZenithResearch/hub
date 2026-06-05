# ISS-P21-002: Queue and case provenance

> Issue = PR boundary. Tasks below = commit boundaries inside that PR.

## PR boundary

- **PR scope:** ISS-P21-002: Queue and case provenance
- **Suggested branch:** `issue/iss-p21-002-queue-and-case-provenance`
- **Suggested PR title:** `ISS-P21-002: Queue and case provenance`
- **Primary repo:** `ZenithResearch/hub`
- **Supporting repo/API dependency:** none identified for this issue note
- **Source vault note:** `private source note: iss-p21-002`
- **GitHub issue:** https://github.com/ZenithResearch/hub/issues/46
- **Repo-local spec path:** `docs/issues/matrix-synapse-v0/iss-p21-002-queue-and-case-provenance.md`

## Full spec

### Objective

Ensure queue items and cases retain Matrix provenance without dumping unrelated room history.

### Repo rationale

Hub owns Matrix ingest classification, queue/case provenance, vanilla auth boundary, ordinary execution, and E2E smoke.

### Dependencies / blocked by

- PRP-PR-018 provenance shape, ISS-P21-001

### Target files and surfaces

- services/ingest/normalizer.py, queue service, services/cases/main.py, eventbus schemas, tests

### Locked decisions and invariants

- V0 Matrix work uses ordinary routing: passive, mention, concierge, and optionally agent-visible.
- Admin/provisioning requests remain behind existing Hub/Gateway vanilla auth.
- Matrix sender identity is provenance/context, not Hub authority.

### Acceptance criteria

- Queue/case payloads include scoped Matrix provenance; unrelated room history/raw transcripts absent; provenance survives queue-to-case.
- Evidence is recorded in the implementation repo or linked capture before this issue is marked complete.
- The project note is updated with the completion evidence and any downstream blockers.

### Verification commands

- pytest services/ingest services/cases tests; ruff.

### Forbidden claims / non-goals

- Do not claim production deployment unless live deploy evidence exists.
- Do not claim Matrix identity is Hub authority.
- Do not print or persist raw appservice/admin/reviewer secrets.
- Do not claim wallet, secS-magik, or Dregg-backed authorization for v0.

## Task list — commit boundaries

Each checked task should land as a separate commit on the PR branch. Do not combine tasks unless the diff is mechanically inseparable; if combined, explain why in the PR body.

### Task 1: Scope and baseline evidence

**Commit boundary:** one commit in the `ISS-P21-002` PR.

**Objective:** Read the source vault note and inspect the target repo surfaces for `ISS-P21-002`. Confirm the exact files/modules to touch, record current behavior, and update this spec if discovery changes the file list.

**Files / surfaces:**
- services/ingest/normalizer.py, queue service, services/cases/main.py, eventbus schemas, tests

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "docs: scope iss-p21-002"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 2: Contract / failing test or guard

**Commit boundary:** one commit in the `ISS-P21-002` PR.

**Objective:** Add the smallest failing test, static check, fixture, or documentation guard that proves the issue is not already complete and captures the desired behavior before implementation.

**Files / surfaces:**
- services/ingest/normalizer.py, queue service, services/cases/main.py, eventbus schemas, tests

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "test: cover iss-p21-002 contract"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 3: Implement the primary behavior

**Commit boundary:** one commit in the `ISS-P21-002` PR.

**Objective:** Make the minimal production change for the objective. Keep the diff limited to this issue's PR boundary and do not pull in adjacent phase work.

**Files / surfaces:**
- services/ingest/normalizer.py, queue service, services/cases/main.py, eventbus schemas, tests

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "feat: implement iss-p21-002"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 4: Negative cases and edge behavior

**Commit boundary:** one commit in the `ISS-P21-002` PR.

**Objective:** Add fail-closed, non-leakage, duplicate/idempotency, unavailable-dependency, or no-op cases relevant to this issue. If the issue is documentation-only, add explicit forbidden examples instead.

**Files / surfaces:**
- services/ingest/normalizer.py, queue service, services/cases/main.py, eventbus schemas, tests

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "test: harden iss-p21-002 edge cases"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 5: Docs, operator notes, and evidence hooks

**Commit boundary:** one commit in the `ISS-P21-002` PR.

**Objective:** Update repo-local docs/runbooks/config comments so an operator or future agent can verify the behavior without reading the vault. Add evidence placeholders or command examples, but do not commit secrets or live tokens.

**Files / surfaces:**
- services/ingest/normalizer.py, queue service, services/cases/main.py, eventbus schemas, tests

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "docs: record iss-p21-002 operator evidence"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 6: PR readiness verification

**Commit boundary:** one commit in the `ISS-P21-002` PR.

**Objective:** Run the verification commands below, run `git diff --check`, inspect the PR diff for scope creep/secrets, and update the PR body with evidence and explicit non-claims.

**Files / surfaces:**
- services/ingest/normalizer.py, queue service, services/cases/main.py, eventbus schemas, tests

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "chore: verify iss-p21-002 pr readiness"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.

## PR body checklist

Before opening or marking the PR ready, include:

- [ ] Link to this repo-local spec.
- [ ] Link to source vault note `private source note: iss-p21-002`.
- [ ] Summary of the implementation.
- [ ] Task/commit list with commit SHAs.
- [ ] Verification commands and results.
- [ ] Explicit forbidden claims that remain false.
- [ ] Supporting repo/API dependency status, if any.

## Related

- [[prp-pr-021|PRP-PR-021: Matrix-triggered vanilla-auth work pipeline]]
- [[../capture/2026-06-04-matrix-wallet-extension-initiative|Matrix production and vanilla auth initiative]]
- [[projects]]
- [[Zenith]]

Areas:
- [[Zenith]]
- [[projects]]
