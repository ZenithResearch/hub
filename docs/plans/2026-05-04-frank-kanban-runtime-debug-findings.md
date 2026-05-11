# Frank Kanban Runtime Debug Findings — Franklin8 Local Reproduction

Date: 2026-05-04
Scope: Final Acceptance / Live E2E debugging for Frank Kanban runtime after repeated Franklin test cases.

## Case Reproduced

Live case inspected via cases HTTP API:

- Case: `case_b1cc97ea05f344cd980b2590920dfad9`
- Title: `review_submitted from Franklin8`
- Process: `review_submitted` / `process-queued-review`
- Live state at freeze:
  - `step_1`: `READY`
  - `step_2`–`step_9`: `PENDING`
  - Filled slots: root inputs only (`review_id`, `audio_asset_id`, `events_asset_id`, `subject_id`, `submitted_by`, `reviewed_at`, `duration_ms`)
  - No case logs persisted because launch failed before Step 1 completion/log append.

Docker/Frank diagnostic log showed:

```text
OSError: [Errno 7] Argument list too long: 'hermes'
```

Failure site:

```text
services/frank/main.py complete_deterministic_review_setup_via_kanban()
→ HermesCliKanbanAdapter.complete()
→ hermes kanban complete ... --metadata <json>
→ asyncio.create_subprocess_exec(...)
```

## Local Reproduction

A local harness imported `services.frank.main`, fetched the Franklin8 persisted case/dispatch packet from `http://127.0.0.1:8083`, fetched the review assets through `http://127.0.0.1:8080`, and executed the deterministic Step 1 setup path against fake Kanban/repository objects.

Observed Franklin8 data:

- Step 1 DB row: `step_6993f68d40f54cb18486ec7508826fca`
- Step 1 Kanban task: `t_b6e18bda`
- Deterministic Step 1 outputs:
  - `review_id_short`
  - `audio_asset_path`
  - `events`
- Event count: `959`
- Old inline Kanban metadata size: `159,141` bytes
- Full cases output payload size: `178,519` bytes
- New compact Kanban metadata size after patch: `921` bytes

The local reproduction confirms the root cause is not STT, worker execution, or parent gating. It is Step 1 control-plane completion trying to send large process data through CLI argv.

## Root Cause

The current architecture uses Kanban task completion metadata for two different jobs:

1. Worker/task audit handoff metadata.
2. Durable case output transfer.

That is unsafe for large outputs. `events` is durable case state and can be large. Passing it via `hermes kanban complete --metadata` makes it part of the OS argument vector. Franklin8's 959 event records produced ~159KB of JSON metadata, which was enough to trigger `Errno 7` in the running container.

The broader design smell: Frank is using Hermes Kanban as both orchestration/audit projection and as a data plane. Cases should remain the data plane. Kanban metadata should stay small and pointer-based.

## Current Patch State

A narrow blocker patch is staged:

- Step 1 deterministic setup writes full outputs directly to the cases service via `repository.complete_step_outputs(...)`.
- Step 1 Kanban completion metadata now carries compact pointers:
  - `outputs_artifact`
  - `outputs_sha256`
- The actual Kanban run id is read back from `kanban.runs(task_id)` and recorded in Step 1 runtime state as `last_reconciled_run_id`.

This unblocks the argv failure but should be treated as a tactical repair, not the final architecture.

## Architecture Finding

The runtime needs an explicit separation:

### Cases service

Authoritative state/data plane:

- root inputs
- step statuses
- durable slots
- large outputs (`events`, transcript words, observations, review docs)
- output validation and idempotency

### Hermes Kanban

Projection/control/audit plane:

- task identity
- dependencies/parent gating
- assignee/workspace/skills
- run status/outcome
- compact audit pointers and hashes
- no large inline output payloads

### Frank reconciler

Boundary adapter:

- validates trusted `metadata.zenith`
- reads small metadata and artifact pointers
- commits outputs to cases only through the cases API
- never assumes fake repo shape equals live HTTP shape
- treats `status=done` + `outcome=completed` as terminal success

## Recommended Update Before More Live Runs

Stop patching one symptom at a time and make one explicit architecture pass:

1. Define a formal Kanban completion metadata schema with max-size expectations.
2. Add adapter-level guardrails:
   - reject or artifactize metadata over a conservative threshold before invoking subprocess.
   - log the field/size that would exceed argv, without printing secrets or full payloads.
3. Make deterministic/control-plane Frank steps commit directly to cases and use Kanban only for compact completion markers.
4. Make worker/model-backed steps either:
   - return small outputs inline when safe, or
   - write large outputs to artifacts and include `outputs_artifact` + `outputs_sha256` for Frank to hydrate and validate.
5. Update `reconcile_kanban_case()` to support `outputs_artifact` hydration for worker outputs, not just Step 1 special casing.
6. Add tests for:
   - large Step 1 `events` does not enter CLI metadata argv.
   - `done` + `completed` worker run is reconciled.
   - live HTTP `steps[].runtime_state_json` idempotency is respected.
   - oversized worker output metadata is rejected or hydrated from artifact.
7. Only after those pass, run a fresh Franklin9-style E2E.

## Do Not Do

- Do not dispatch the contaminated Franklin8 case from host.
- Do not rely on host-side Hermes dispatch for container-path workspaces.
- Do not keep adding case-specific special cases without first formalizing the data-plane/control-plane split.
- Do not pass raw event streams, transcripts, or observations through CLI arguments.
