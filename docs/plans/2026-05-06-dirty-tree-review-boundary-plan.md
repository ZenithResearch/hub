# Dirty Tree Review Boundary Plan

## Goal

Cut the current Hub dirty rescue tree into named, reviewable work surfaces instead of continuing feature work inside a mixed state.

This is primarily an obsolescence filter, not a reconciliation pass. Only dirty work that can be defended as still relevant, exercised, and necessary should become a commit candidate. If a surface cannot be defended strongly, it should be left out, reverted later with explicit authorization, or documented as obsolete/debt — not normalized into the codebase.

The desired output is a sequence of small, mechanically defensible checkpoints where each commit has a named surface, explicit included paths, focused verification, and an artifact scan.

## Current clean boundary

Last completed focused commit:

- `31fda1a feat: harden Frank review packet delegation quality`

That commit closed the Frank review-packet delegation-quality V2 thread. The remaining dirty tree is broader rescue work and must not be normalized into one commit.

## Core rules

0. Defend-or-discard standard.
   - A dirty change is not a candidate just because it is coherent, nearby, or testable.
   - To stay in the codebase, it must pass the defense test: What ran? What production/current path needs it? What breaks if it is removed? Which commit or live run proves it matters?
   - If the answer is weak, classify it as obsolete/not-a-candidate before considering tests or staging.

1. Do not broad-stage.
   - Never use `git add -A`.
   - Use explicit path staging or patch staging only.

2. Treat staged diff as the review object.
   - `git diff --cached --stat` must match the named surface.
   - `git diff --cached --name-status` must have no unrelated files.

3. A work-surface index names terrain; it does not excuse debt.
   - Every surface must be completed, intentionally deferred, or marked as debt requiring cleanup.
   - No silent approach switching.

4. Mixed files require hunk cutting.
   - If a file contains multiple surfaces, save the current overlay to `/tmp/hub-dirty-boundary/<path>`, restore to HEAD, reapply only the intended hunk, commit, then restore the overlay if preserving rescue state is required.

5. Runtime/local artifacts stay out.
   - Staged artifact scan must be empty.

6. CHANGELOG is mixed.
   - Do not stage it wholesale.
   - Only include focused changelog hunks after a surface is selected and verified.

## Dirty tree inventory

Observed after commit `31fda1a`:

### Surface A — Frank runtime simplification / Kanban removal

Likely checkpoint type: deletion/simplification, not preservation.

Current conclusion:
- The current/default Frank execution mode is `native_case_pipeline`.
- Compose no longer wires `FRANK_RUNTIME` or any `FRANK_KANBAN_*` environment into `frank`; service code defaults to `native_case_pipeline` and rejects `kanban`/`direct` if explicitly supplied.
- The review-packet production path that ran and was committed uses `CasePipelineRunner`.
- The worker queue consumes worker assignment messages directly; it does not require Hermes Kanban.
- `FRANK_RUNTIME=kanban` and `FRANK_RUNTIME=direct` are obsolete, not dormant supported modes.
- Verification on 2026-05-06 after simplification: `ruff check` for Frank/runtime tests passed; `py_compile` passed; `tests/test_frank_dispatcher.py tests/test_phase11_docker_e2e_contract.py tests/test_frank_case_pipeline_runner.py tests/test_process_contract.py tests/test_review_packet.py tests/test_review_packet_acceptance_script.py -q` passed with `89 passed, 6 subtests`; `docker compose config --quiet` passed.

Candidate files simplified/removed in Surface A:
- `services/frank/main.py` alternate runtime imports/branches and direct step-runner executor code
- `docker-compose.yml` Frank alternate runtime env wiring
- `docker/frank/Dockerfile` and `docker/hermes_worker_queue/Dockerfile` Hermes Kanban patch copies
- `services/frank/hermes_cli_kanban.py`
- `services/frank/kanban_client.py`
- `services/frank/kanban_projection.py`
- `services/frank/kanban_reconciler.py`
- `services/frank/kanban_slice_materializer.py`
- `services/frank/case_repository.py`
- `services/frank/step_runner.py`
- `scripts/quickstart_frank_kanban.sh`
- `tests/fixtures/frank_runtime/*`
- `tests/test_frank_hermes_cli_kanban.py`
- `tests/test_frank_kanban_client.py`
- `tests/test_frank_kanban_projection.py`
- `tests/test_frank_kanban_reconciler.py`
- `tests/test_frank_kanban_slice_materializer.py`
- `tests/test_frank_case_repository.py`
- `tests/test_frank_runtime_fixtures.py`
- `tests/test_frank_step_runner.py`
- Kanban/direct-specific expectations in `tests/test_phase11_docker_e2e_contract.py` and `tests/test_frank_dispatcher.py`
- Frank active profile prompt at `rolodex/agents/frank/SOUL.md`

Risk:
- Medium/high because deletion can expose stale references.

Boundary requirement:
- Do not preserve Kanban because it is coherent or historically implemented.
- Preserve only if a current runtime path requires it. Current evidence says it does not.

Target simplification:
- Remove `kanban` from valid Frank runtime modes.
- Make `native_case_pipeline` the only normal review execution runtime.
- Keep `direct` only if a current, explicit fallback is still necessary and defensible; otherwise remove it too.
- Remove `hermes_kanban` dispatch packet generation.
- Remove Kanban launch/reconciliation/materialization branches from active Frank execution.
- Delete obsolete Kanban-only tests/fixtures after reference checks pass.

Verification candidates:
- `.venv/bin/python -m py_compile services/frank/main.py services/frank/case_pipeline_runner.py`
- `.venv/bin/python -m pytest tests/test_frank_case_pipeline_runner.py tests/test_process_contract.py tests/test_review_packet.py tests/test_review_packet_acceptance_script.py -q`
- targeted dispatcher tests after removing/rewriting Kanban expectations

### Surface B — Runtime containers and startup

Likely checkpoint type: Docker/startup wiring.

Files:
- `docker/frank/Dockerfile`
- `docker/hermes_worker_queue/Dockerfile`
- `docker/hermes_agent_patches/kanban.py`
- `docker/hermes_agent_patches/kanban_db.py`
- `scripts/start.sh`

Risk:
- Medium/high. Container patch files may be required by Frank Kanban runtime, but they may also be an independent infrastructure patch.

Boundary question:
- Are these patch files used by the current runtime path or only by the older Kanban rescue path?

Verification candidates:
- `docker compose config --quiet`
- targeted compose contract tests if tied to Phase 11/12

### Surface C — Cases observability/state API

Likely checkpoint type: service API + tests.

Files:
- `services/cases/main.py`
- `tests/test_cases_observability.py`

Risk:
- Medium. `services/cases/main.py` has a large diff; needs API-level inspection before staging.

Boundary requirement:
- Confirm whether this is pure observability/read API work or includes state mutation/persistence changes.

Verification candidates:
- `.venv/bin/python -m pytest tests/test_cases_observability.py -q`
- existing cases service tests if present
- py_compile for `services/cases/main.py`

### Surface D — Eventbus broker/http observability

Likely checkpoint type: eventbus diagnostics/transport behavior.

Files:
- `services/eventbus/broker.py`
- `services/eventbus/http.py`
- `tests/test_eventbus_broker.py`

Risk:
- Medium. Eventbus changes can affect live wake/reconciliation behavior.

Verification candidates:
- `.venv/bin/python -m pytest tests/test_eventbus_broker.py -q`
- py_compile for eventbus files

### Surface E — Gateway sessions/dashboard UI

Likely checkpoint type: Gateway API + dashboard UI.

Files:
- `services/gateway_http/app.py`
- `services/gateway_http/static/dashboard.html`
- `tests/test_gateway_http_sessions.py`

Risk:
- Medium/high because `services/gateway_http/app.py` has known unrelated overlays from previous work.

Boundary requirement:
- Inspect carefully. Do not accidentally mix already-committed follow-up endpoint work, session work, dashboard work, or unrelated Gateway changes.

Verification candidates:
- `.venv/bin/python -m pytest tests/test_gateway_http_sessions.py -q`
- `.venv/bin/python -m py_compile services/gateway_http/app.py`

### Surface F — Matrix bot infrastructure

Likely checkpoint type: Matrix deployment/config.

Files:
- `infra/matrix/config/homeserver.yaml`
- `infra/matrix/docker-compose.yml`
- `scripts/setup_matrix_bots.sh`

Risk:
- Medium. Config may contain local secrets/tokens or environment-sensitive values.

Boundary requirement:
- Secret scan before any commit.
- Confirm changes are generalized, not local-only.

Verification candidates:
- `docker compose -f infra/matrix/docker-compose.yml config --quiet`
- shellcheck if available for setup script

### Surface G — Agent rolodex Frank/Sophia responsibility split

Likely checkpoint type: agent identity/config/skill migration.

Files:
- `rolodex/agents/frank/SOUL.md`
- `rolodex/agents/frank/config.yaml`
- `rolodex/agents/frank/skills/dispatch-work.md`
- `rolodex/agents/frank/skills/generate-proc-dag.md`
- `rolodex/agents/frank/skills/match-process.md`
- `rolodex/agents/frank/skills/process-request.md`
- `rolodex/agents/sophia/SOUL.md`
- `rolodex/agents/sophia/Sophia.md`
- `rolodex/agents/sophia/config.yaml`
- `rolodex/agents/sophia/skills/case-execution-loop.md` deleted
- `rolodex/agents/sophia/skills/step-execution-loop.md` deleted
- `rolodex/agents/sophia/skills/case-execution-loop/SKILL.md`
- `rolodex/agents/sophia/skills/case-execution-loop/scripts/fetch_review_assets.py`
- `rolodex/agents/sophia/skills/case-execution-loop/scripts/worker_cli.py`
- `rolodex/agents/sophia/skills/step-execution-loop/SKILL.md`
- `rolodex/index.yaml`

Risk:
- Medium/high. This surface changes agent responsibilities and skill layout.

Boundary question:
- Are the deleted flat skill files intentional migrations into directory-style skills, or stale rescue deletions?

Verification candidates:
- YAML parse for configs/index.
- Skill file existence/reference checks.
- Any rolodex validation tooling if available.

### Surface H — Historical docs/plans backlog

Likely checkpoint type: documentation triage, not feature code.

Files:
- `docs/asset-fetch-and-session-artifacts-plan-2026-04-30.md`
- `docs/e2e-review-submission-report-2026-04-29.md`
- `docs/frank-native-case-pipeline.md`
- `docs/frank-sophia-runtime-transition-layers.md`
- `docs/hermes-forward-strategy-report-2026-04-29.md`
- `docs/review-dispatch-object-trace-2026-04-30.md`
- `docs/soul-skills-prompts-report-2026-04-30.md`
- `docs/worker-orchestration-brainstorm-memo-2026-04-30.md`
- `docs/worker-orchestrator-implementation-plan-2026-05-01.md`

Risk:
- Low/medium. Some may be stale and should not be committed simply because they exist.

Boundary requirement:
- Decide per document: active reference, stale plan, or delete/leave untracked.

### Surface I — CHANGELOG mixed ledger

Likely checkpoint type: hunk-only after each accepted surface.

File:
- `CHANGELOG.md`

Risk:
- High for accidental broad commit. It currently includes mixed changes from multiple prior surfaces.

Rule:
- Never stage whole file until the dirty tree is reduced or each hunk is accounted for.

## Recommended execution order

### Phase 0 — Freeze and map

Purpose:
- Produce a permanent review-boundary index and prevent more feature edits.

Actions:
1. Keep this plan as the active index.
2. Run status and diff-stat snapshots.
3. Create a per-surface checklist document only if needed.

Exit gate:
- Plan approved by user.
- No staging yet.

### Phase 1 — Obsolescence defense triage

Purpose:
- Decide what is even eligible for review. This happens before tests, before staging, and before any attempt to make a dirty surface look coherent.

For each surface, answer:
1. Did this exact surface run successfully, or is it only speculative rescue work?
2. Is it required by the current committed Frank path after `31fda1a`?
3. Does any current production or local acceptance path depend on it?
4. What breaks if this surface is removed/reverted?
5. Is the change already superseded by a later committed path?
6. Can we defend keeping it vehemently?

Classification labels:
- `KEEP-CANDIDATE`: defensible; proceed to focused diff/test review.
- `SPLIT-FIRST`: some defensible hunks exist, but the surface is mixed.
- `OBSOLETE`: superseded by committed work or no longer aligned with current path.
- `UNKNOWN`: insufficient evidence; inspect logs/tests/docs before deciding.
- `DROP-LATER`: likely not worth keeping, but do not delete/reset without explicit authorization.

Important Frank standard:
- For Frank-specific dirty changes, the default assumption is `OBSOLETE` unless tied to a run, a currently committed production path, or a directly necessary support surface. The fact that something belongs to the old Kanban runtime rescue tree is not enough.

### Phase 2 — Low-risk independent API surfaces

Start with isolated surfaces that have clear tests and small dependency radius.

Order:
1. Surface D — Eventbus broker/http observability.
2. Surface C — Cases observability/state API.
3. Surface E — Gateway sessions/dashboard UI, only after hunk inspection.

Reason:
- These have obvious file/test pairs and can be converted into reviewable commits without first resolving the larger Frank Kanban runtime story.

Per-surface loop:
1. Reset staging: `git restore --staged .`.
2. Inspect full diff for only that surface.
3. Decide whether it is one checkpoint or must split further.
4. Run focused tests.
5. Stage explicit files only.
6. Run gates:
   - `git diff --cached --stat`
   - `git diff --cached --name-status`
   - artifact scan
   - `git diff --cached --check`
7. Optional subagent review for medium/high risk surfaces.
8. Commit if tests and review pass.
9. Report remaining dirty surfaces.

### Phase 2 — Agent responsibility/config migration

Surface G.

Reason:
- The Frank/Sophia rolodex changes describe who owns runtime work and should be cut before or alongside runtime commits, but only after independent API surfaces are out of the way.

Additional gates:
- Validate YAML.
- Validate skill paths/references.
- Confirm deleted flat skill files are replaced by directory-style skills.

### Phase 3 — Runtime containers/startup dependency surface

Surface B.

Reason:
- Container and patch-file changes likely support Frank Kanban runtime. They should be committed either as a prerequisite infrastructure patch or folded into a tightly named runtime sub-checkpoint after proving dependency.

Additional gates:
- Verify Dockerfile patch copies refer to tracked files.
- `docker compose config --quiet` if compose path affected.
- No local-only secrets or machine-specific paths.

### Phase 4 — Frank Kanban runtime core split

Surface A.

Reason:
- Largest and riskiest surface. Do not attempt as one broad commit unless inspection proves it is a single coherent checkpoint.

Proposed sub-phases:
1. Hermes CLI Kanban adapter compatibility.
2. Kanban client/projection/materializer fixture alignment.
3. Reconciler behavior.
4. Frank main runtime switch/orchestration.
5. Docker/E2E contract tests.

Each sub-phase gets its own staged object and test gate.

### Phase 5 — Docs backlog triage

Surface H.

Reason:
- Historical docs should not ride along with code commits. After code surfaces are cut, decide which docs still describe current architecture.

Possible outcomes per doc:
- Commit as active reference.
- Rewrite into current reference.
- Leave untracked.
- Delete only with explicit authorization.

### Phase 6 — CHANGELOG reconciliation

Surface I.

Reason:
- Once surfaces are committed, either patch CHANGELOG per commit or leave it dirty until the whole rescue tree is reduced. Never use it to smuggle unrelated surface claims.

## Mechanical gate template

For each candidate commit:

```bash
cd /Users/bananawalnut/repos/hub
git restore --staged .
# stage explicit paths only
git diff --cached --stat
git diff --cached --name-status
git diff --cached --name-only | grep -E '(^|/)(\.hermes|\.tmp|logs?|sessions?|auth|.*\.db(-shm|-wal)?$|.*\.lock$|__pycache__|\.pyc$|data/)' || true
git diff --cached --check
# run focused tests for the surface
```

Expected:
- Staged stat matches one named surface.
- Artifact scan is empty.
- Tests pass.
- No unrelated dirty files are staged.

## First recommended cut
First recommended action after plan approval:

Run Phase 1 obsolescence defense triage, not Eventbus staging.

Recommended triage order:
1. Frank runtime simplification first, because the user wants only the currently working runtime execution mode:
   - Surface A — remove/delist dormant Kanban runtime branches and tests
   - Surface B — keep only container/startup changes required by native runtime; otherwise obsolete
   - Surface G — keep only Frank/Sophia role changes required by native runtime; otherwise obsolete
2. Then independent service surfaces:
   - Surface D — Eventbus
   - Surface C — Cases
   - Surface E — Gateway
3. Then Matrix/docs/changelog surfaces.

Initial command set for Frank triage:

```bash
cd /Users/bananawalnut/repos/hub
git diff --stat -- services/frank docker/frank docker/hermes_worker_queue docker/hermes_agent_patches scripts/start.sh rolodex/agents/frank rolodex/agents/sophia rolodex/index.yaml tests/test_frank_* tests/fixtures/frank_runtime tests/test_phase11_docker_e2e_contract.py
```

Then inspect each Frank candidate and fill a defense table:

| Surface | Classification | Ran? | Current committed path needs it? | Breakage if removed | Defense |
|---|---|---:|---:|---|---|
| A Frank runtime simplification / Kanban removal | KEEP-CANDIDATE | Native path ran; Kanban did not | Native path does not need Kanban | Stale explicit `FRANK_RUNTIME=kanban` users lose unsupported mode | Simplifies to only currently working runtime |
| B Runtime containers/startup | UNKNOWN | TBD | Keep only native runtime container wiring | TBD | TBD |
| G Agent rolodex split | UNKNOWN | TBD | Keep only native runtime role instructions | TBD | TBD |

Only after a surface gets `KEEP-CANDIDATE` or `SPLIT-FIRST` should we run its tests or stage hunks.

## Stop conditions

Stop and report instead of committing if:
- A surface diff contains two or more unrelated concepts.
- Tests fail for reasons unrelated to the surface.
- A required file dependency is untracked and not understood.
- Any staged path matches runtime/artifact/secret patterns.
- A surface requires broad reset or deletion to make sense.
- A service API or persistence contract changes without matching tests.
