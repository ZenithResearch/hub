# ISS-P21-005: End-to-end vanilla-auth smoke

> Issue = PR boundary. Tasks below = commit boundaries inside that PR.

## PR boundary

- **PR scope:** ISS-P21-005: End-to-end vanilla-auth smoke
- **Suggested branch:** `issue/iss-p21-005-end-to-end-vanilla-auth-smoke`
- **Suggested PR title:** `ISS-P21-005: End-to-end vanilla-auth smoke`
- **Primary repo:** `ZenithResearch/hub`
- **Supporting repo/API dependency:** none identified for this issue note
- **Source vault note:** `private source note: iss-p21-005`
- **GitHub issue:** https://github.com/ZenithResearch/hub/issues/49
- **Repo-local spec path:** `docs/issues/matrix-synapse-v0/iss-p21-005-end-to-end-vanilla-auth-smoke.md`

## Full spec

### Objective

Prove passive stays passive, mention/concierge enqueue bounded work, replies correlate, and admin/provisioning requests fail closed without Hub/Gateway auth.

### Repo rationale

Hub owns Matrix ingest classification, queue/case provenance, vanilla auth boundary, ordinary execution, and E2E smoke.

### Dependencies / blocked by

- ISS-P21-001..004, PRP-PR-018

### Target files and surfaces

- Hub integration tests/smoke scripts, ingest fixtures, queue/cases fixtures, outbound Matrix adapter, docs/runbooks

### Locked decisions and invariants

- V0 E2E is ordinary Matrix work plus vanilla Hub/Gateway auth boundaries.
- Matrix sender identity is never standalone Hub authority.

### Acceptance criteria

- Smoke covers passive/no case, mention/concierge queue, reply correlation, and admin/provisioning deny without Hub/Gateway auth; outputs redact tokens.
- No wallet, secS-magik, or Zenith Review SDK wallet-auth test is required for v0.
- Evidence is recorded in the implementation repo or linked capture before this issue is marked complete.

### Verification commands

- hub pytest; focused ingest/queue/outbound smoke; optional production smoke only with explicit deploy evidence.

### Forbidden claims / non-goals

- Do not claim production deployment unless live deploy evidence exists.
- Do not claim Matrix identity is Hub authority.
- Do not print or persist raw appservice/admin/reviewer secrets.
- Do not claim wallet, secS-magik, or Dregg-backed authorization for v0.

## Task list — commit boundaries

Each checked task should land as a separate commit on the PR branch. Do not combine tasks unless the diff is mechanically inseparable; if combined, explain why in the PR body.

### Task 1: Scope and baseline evidence

**Commit boundary:** one commit in the `ISS-P21-005` PR.

**Objective:** Read the source vault note and inspect the target repo surfaces for `ISS-P21-005`. Confirm the exact files/modules to touch, record current behavior, and update this spec if discovery changes the file list.

**Files / surfaces:**
- Hub integration tests/smoke scripts, ingest fixtures, queue/cases fixtures, outbound Matrix adapter, docs/runbooks

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "docs: scope iss-p21-005"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 2: Contract / failing test or guard

**Commit boundary:** one commit in the `ISS-P21-005` PR.

**Objective:** Add the smallest failing test, static check, fixture, or documentation guard that proves the issue is not already complete and captures the desired behavior before implementation.

**Files / surfaces:**
- Hub integration tests/smoke scripts, ingest fixtures, queue/cases fixtures, outbound Matrix adapter, docs/runbooks

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "test: cover iss-p21-005 contract"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 3: Implement the primary behavior

**Commit boundary:** one commit in the `ISS-P21-005` PR.

**Objective:** Make the minimal production change for the objective. Keep the diff limited to this issue's PR boundary and do not pull in adjacent phase work.

**Files / surfaces:**
- Hub integration tests/smoke scripts, ingest fixtures, queue/cases fixtures, outbound Matrix adapter, docs/runbooks

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "feat: implement iss-p21-005"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 4: Negative cases and edge behavior

**Commit boundary:** one commit in the `ISS-P21-005` PR.

**Objective:** Add fail-closed, non-leakage, duplicate/idempotency, unavailable-dependency, or no-op cases relevant to this issue. If the issue is documentation-only, add explicit forbidden examples instead.

**Files / surfaces:**
- Hub integration tests/smoke scripts, ingest fixtures, queue/cases fixtures, outbound Matrix adapter, docs/runbooks

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "test: harden iss-p21-005 edge cases"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 5: Docs, operator notes, and evidence hooks

**Commit boundary:** one commit in the `ISS-P21-005` PR.

**Objective:** Update repo-local docs/runbooks/config comments so an operator or future agent can verify the behavior without reading the vault. Add evidence placeholders or command examples, but do not commit secrets or live tokens.

**Files / surfaces:**
- Hub integration tests/smoke scripts, ingest fixtures, queue/cases fixtures, outbound Matrix adapter, docs/runbooks

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "docs: record iss-p21-005 operator evidence"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.
### Task 6: PR readiness verification

**Commit boundary:** one commit in the `ISS-P21-005` PR.

**Objective:** Run the verification commands below, run `git diff --check`, inspect the PR diff for scope creep/secrets, and update the PR body with evidence and explicit non-claims.

**Files / surfaces:**
- Hub integration tests/smoke scripts, ingest fixtures, queue/cases fixtures, outbound Matrix adapter, docs/runbooks

**Steps:**
1. Inspect the named files/surfaces and keep the diff limited to this issue.
2. Make only the change required for this task.
3. Run the narrowest relevant verification command before committing.
4. Commit with:

```bash
git add <changed-files>
git commit -m "chore: verify iss-p21-005 pr readiness"
```

**Done when:** this task's change is independently reviewable and the next task can build on it without rewriting it.

## PR body checklist

Before opening or marking the PR ready, include:

- [ ] Link to this repo-local spec.
- [ ] Link to source vault note `private source note: iss-p21-005`.
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
