---
name: case-execution-loop
description: >
  Frank-owned outer loop for materializing a canonical case DAG into a bounded
  Hermes execution board, launching in-case workers, and reconciling results.
version: "1.1.0"
---

# case-execution-loop

## Purpose

Frank owns case orchestration. Zenith's cases service remains the source of truth
for case state, step readiness, dependencies, policy, and completion. Hermes
Kanban is the in-case execution contract: Frank compiles the process DAG into a
case-scoped board, launches profile workers against that board, and reconciles
operational results back into the cases service.

This skill is the canonical worker execution loop. It may still be used by the
legacy `hermes-worker-queue` compatibility bridge until a live Hermes Kanban
adapter is available, but Sophia is no longer the canonical owner of case
execution.

## Inputs

- `case_id`
- Frank-owned dispatch packet
- resolved case/process DAG
- resolved step briefs
- assigned profile / seat for each task
- workspace policy for each task
- `scripts/worker_cli.py`

## Loop

1. Load the case with `worker_cli.py load-case --case-id ...`.
2. Treat `case.dispatch_packet_json` as Frank's compiled execution contract.
3. Materialize or validate the case-scoped Hermes Kanban board plan. Each process
   step maps to one task; each in-case dependency edge maps to one Hermes-native
   task link.
4. Write initial payload-derived slots from `initial_context` only through the
   deterministic parent/orchestrator path. Slots are write-once; only write values
   that are still empty.
5. Execute deterministic setup boundaries in the Frank-owned parent path when the
   process declares them, then recompute runnable tasks from live slot state.
6. For each runnable task in the current board wave:
   - use the exact `resolved_step_brief`
   - include current input slot values
   - include the assigned profile / seat
   - include workspace policy
   - include expected output names and types
   - persist runtime/task state while work is active
7. Launch bounded profile workers for task work. Workers execute inside the case
   board; they do not redefine the DAG and do not directly mutate canonical case
   state.
8. Each worker returns a structured JSON envelope only.
9. Frank validates returned outputs against the declared output schema and commits
   them durably to the cases service.
10. If a worker fails, Frank records the failure, decides retry/reroute/escalation,
    and reconciles case state.
11. Continue until no runnable tasks remain or the case reaches a terminal state.

## Structured Result Contract

Each in-case worker must return:

```json
{
  "status": "completed",
  "step_db_row_id": "step_db_3",
  "outputs": {
    "example_output": "value"
  },
  "notes": [
    "Optional progress or provenance notes."
  ]
}
```

On failure:

```json
{
  "status": "failed",
  "step_db_row_id": "step_db_3",
  "reason": "Human-readable failure reason.",
  "notes": [
    "Optional debugging context."
  ]
}
```

Only `outputs` is authoritative for successful result data. Frank/the parent
orchestrator commits outputs durably.

## Review Asset Materialization

When the case includes `events_asset_id` and `audio_asset_id`, do not assume the
review assets already exist under `/hub/data/reviews/assets` or any other shared
filesystem path.

Before asset-dependent work:

1. Run `worker_cli.py materialize-assets --case-id ...` from this skill directory,
   which calls `fetch_review_assets.py` deterministically.
2. Pass:
   - `--case-id`
   - optional `--output-dir`
3. Use the returned local paths as the authoritative materialized asset paths for
   the current run.

The helper prints JSON with:

- `events_asset_path`
- `audio_asset_path`
- `materialized_dir`

## Rules

- Frank owns orchestration, case policy, DAG compilation, board materialization,
  lifecycle, and reconciliation.
- Hermes/profile workers execute bounded in-case task work after Frank launches it.
- Zenith remains source of truth; Hermes Kanban state is operational execution state.
- Use Hermes-native links only for dependencies inside the current case.
- Cross-case dependencies stay in Zenith.
- Do not redefine the case DAG or invent new slot names.
- Do not let child workers write case slots directly.
- Do not treat Sophia as a case executor; Sophia is comms/publication-facing.
- Use `worker_cli.py` for deterministic runtime transitions and durable writes.
- Keep runtime/task state durable on the step row or board task, not only session-local.
