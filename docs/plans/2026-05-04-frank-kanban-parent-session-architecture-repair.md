# Frank Kanban parent-session architecture repair

## Problem

Franklin10 proved the current live Kanban E2E path is functionally closer, but it is using the wrong execution shape.

Observed live behavior:

- Step 2 STT task: one Kanban run, 145s total.
- Actual local STT HTTP call inside the worker log: 8.5s.
- Step 3 component extraction: one Kanban run, 142s total.
- Step 4 transcript annotation: one Kanban run, 186s total.

This is not retry overhead. Each step is launching a fresh full Hermes session via the worker queue:

```text
hermes --skills case-execution-loop,step-execution-loop chat -q "work kanban task <task_id>"
```

That violates the intended architecture.

## Correct architecture

There should be one parent case runner per case execution.

Runner placement decision:

- The durable runner lives in service code, not in a prompt-only chat transcript.
- Initial placement: `services/frank` owns `CaseKanbanParentRunner`, because Frank already owns queue intake, case creation/reuse, process matching, DAG compilation, Kanban materialization, and reconciliation.
- `services/hermes_worker_queue` may still host actual Hermes child reasoning sessions later, but it must not be the component that blindly spawns one session per child step task.
- A Hermes parent session may be used as the reasoning/orchestration surface only if it is launched by the service runner with explicit callable tools for cases/Kanban/delegation and is supervised by service state. It is not the source of durability.

The parent runner owns:

- loading the case once
- materializing/fetching review assets once
- reading the process contract and compiled DAG
- rechecking deterministic DAG readiness before every wave
- approving step starts only when both cases dependencies and Kanban parent gates are satisfied
- executing deterministic/native steps directly when no reasoning is required
- launching reasoning-required child work from the parent boundary, not from global Kanban child-task dispatch
- collecting structured step outputs
- validating outputs against the process contract
- committing accepted outputs to cases
- completing / blocking the corresponding Kanban task with compact metadata
- repeating until no runnable wave remains or the case reaches terminal state

Hermes Kanban remains the durable control/audit plane:

- task identity
- parent/child dependencies
- ready/waiting/completed/blocked state
- run history
- compact completion metadata
- audit pointers/hashes

Cases remains the data plane:

- slots
- large outputs
- step status
- output validation
- durable runtime state
- logs

Kanban should gate step readiness, but it should not cause a new standalone Hermes CLI session per step.

## Current incorrect shape

Current `services/hermes_worker_queue/main.py` launches one process per dequeued worker task:

```python
def build_hermes_command(case_id, profile, prompt):
    command = ["hermes"]
    command.extend(["--skills", ",".join(PRELOADED_SKILL_NAMES)])
    command.extend(["chat", "-q", prompt])
    return command
```

That design makes every Kanban worker task pay full agent startup, skill loading, file discovery, prompt processing, and artifact packaging overhead.

The prompt already gestures at the right model:

```text
Spawn one subagent per runnable step in parallel and persist step runtime/task state while those steps are active.
Before each spawn wave, re-fetch the case and re-evaluate readiness just in time.
Delegated step runners must return structured JSON only; the parent validates and commits wave outputs.
```

But the runtime shape contradicts it by dispatching each materialized step as an independent Hermes session.

## Non-dispatchable child task contract

Child step tasks must be visible in Kanban for durable DAG state and audit, but non-dispatchable by the existing global Hermes Kanban dispatcher.

Invariant:

- The global Kanban dispatcher must never claim or spawn a child step task for a Frank case.
- Child step tasks are parent-owned control records. Only `CaseKanbanParentRunner` may start, complete, or block them.
- It is a bug if logs show `hermes chat -q "work kanban task <child_step_task_id>"` for a Frank child step task.

Allowed mechanisms; choose one and test it explicitly:

1. Unassigned child tasks
   - Materialize child tasks with no assignee / a sentinel assignee that the global dispatcher ignores.
   - Parent runner addresses them directly through the Kanban adapter/tool API.

2. Parent-owned assignee lane
   - Materialize child tasks with assignee such as `frank-parent:<case_id>`.
   - Configure dispatcher preflight to ignore that assignee pattern.
   - Parent runner is the only actor allowed to transition those tasks.

3. Parked child status
   - Materialize child tasks in a non-dispatchable status such as `todo`/`blocked` plus a parent-owned marker.
   - Parent runner promotes/claims/completes them through a dedicated parent-runner path after deterministic readiness approval.
   - Do not rely on ordinary parent-link promotion to `ready` unless dispatcher ignore rules are in place.

Required test fixtures:

- Materialized child task with satisfied parents does not get claimed by the global dispatcher.
- Dispatcher nudge against a Frank case with ready child tasks spawns zero child Hermes sessions.
- Parent runner can still transition the same child task through start/complete/block using the parent-runner path.
- Any child task missing the parent-owned/non-dispatchable marker fails preflight.

## Kanban run semantics for parent-owned child tasks

Even though child tasks are non-dispatchable by the global dispatcher, each completed or blocked child step still needs a durable Kanban run record.

Decision:

- Parent runner creates the child task run record through the Kanban adapter, not by shelling out to a child worker process.
- The run is a control-plane run with `profile`/actor set to the parent runner identity, e.g. `frank-parent-runner` or the parent Hermes session profile.
- The run metadata must include trusted `metadata.zenith` identity and compact handoff data:
  - `case_id`
  - `step_db_row_id`
  - `step_id`
  - `status`
  - `outputs` for small schema outputs, or `outputs_artifact` + `outputs_sha256` when hydration exists
  - `notes`
  - `artifacts`
  - `model_backed: false` for native deterministic steps
  - `audit` only for model-backed delegated reasoning steps

Implementation options:

1. Add parent-runner methods to the adapter:
   - `start_control_run(task_id, actor="frank-parent-runner")`
   - `complete_control_run(task_id, summary, metadata)`
   - `block_control_run(task_id, reason, metadata)`

2. If Hermes Kanban CLI/API can only complete unclaimed tasks today, use `complete(task_id, ...)` as the short-term run-creation mechanism, but tests must prove the returned run id is stable and that no child worker process was spawned.

Acceptance criteria:

- Every completed/blocked child task has exactly one terminal run for the parent-runner attempt.
- The run id is written to cases runtime state as `last_reconciled_run_id` only after cases accepts outputs/status.
- The run profile/actor clearly identifies the parent runner, not an independent child worker session.
- Reconciliation treats native deterministic runs as valid without model audit, and model-backed delegated runs as requiring audit.

## Deterministic/native step contract

Native deterministic steps are first-class step executions, not missing model-backed audits.

Rules:

- Set `metadata.zenith.model_backed = false`.
- Do not require `metadata.zenith.audit` or `upsert_model_task_audit()` for native deterministic steps.
- Persist output artifacts and hashes when outputs are large.
- For small declared outputs, inline `metadata.zenith.outputs` is allowed if it does not risk argv/context blowup.
- Cases remains authoritative: write outputs to cases first, then complete the Kanban task/run, then mark the run reconciled.
- Native executor logs should be appended to cases and linked from `metadata.zenith.artifacts` when durable evidence is useful.

Step-specific examples:

- Step 1 deterministic setup:
  - already follows `model_backed=false`
  - writes full outputs to cases before Kanban completion
  - Kanban metadata stays compact

- Step 2 STT:
  - parent runner calls `stt-http` directly
  - writes `transcript`, `audio_offset_ms`, `words` to cases
  - stores word-level output artifact and `outputs_sha256` if needed
  - completes the Step 2 Kanban task as `model_backed=false`
  - should not require model audit

- Artifact packaging/completion metadata:
  - native code computes hashes and artifact paths
  - no autonomous agent session required

## Required repair

Replace per-step Hermes worker sessions with a parent case-runner.

### Stage 1 — Freeze the contract

Add tests that fail on the current shape:

1. A case launch schedules/starts exactly one parent runner for the case, not one session per step.
2. Step Kanban tasks are materialized but marked non-dispatchable by the global dispatcher.
3. Global dispatcher nudge cannot claim/spawn Frank child step tasks, even after parent dependencies complete.
4. Parent runner computes runnable waves from live case + Kanban task state before each wave.
5. Parent runner only starts child step work when both are true:
   - cases dependencies/slots are satisfied
   - Kanban parents are complete / task is parent-runner-approved
6. Parent runner completes the Kanban child task only after cases accepts the step outputs.
7. Parent runner blocks the Kanban child task if cases rejects outputs or a delegated child returns invalid structured output.
8. Every parent-owned child completion creates a durable run id and stores it in cases runtime state only after successful cases write.

### Stage 2 — Introduce parent runner boundary

Create an explicit service-code parent runner abstraction:

```text
CaseKanbanParentRunner
  - load_case()
  - load_materialized_slice()
  - compute_ready_wave(case, kanban_tasks)
  - approve_step_start(step)  # deterministic DAG + Kanban state check
  - execute_native_step(step) # STT/artifact/schema-shaped deterministic work
  - delegate_reasoning_step(step) # optional Hermes/delegate_task/profile-specific reasoning
  - validate_step_outputs(step, outputs)
  - commit_step_outputs(step, outputs)
  - complete_kanban_step(step, compact_metadata)
  - block_kanban_step(step, compact_metadata)
  - reconcile_loop()
```

For deterministic steps such as STT, the delegated child must be replaced by a native executor; it should not require a general reasoning session.

### Stage 3 — Rewire worker queue / dispatcher boundary

The worker queue should launch or wake the parent case runner for a case assignment, not a fresh session for every step task.

Options:

- Preferred: queue contains one case execution assignment; parent runner uses Kanban as internal durable DAG/audit state.
- Acceptable interim: Kanban root/case task launches parent runner; child tasks are materialized for gating/audit but are unassigned/parked/parent-owned so the global dispatcher ignores them.

Disallow:

```text
hermes chat -q "work kanban task <child_step_task_id>"
```

as the steady-state step execution path.

### Stage 4 — Deterministic speed path

Move deterministic operations out of autonomous child agents:

- Step 1: already deterministic in Frank.
- Step 2 STT: call `stt-http` directly from parent/native executor; expected runtime should be near the observed 8.5s STT call plus packaging.
- Artifact packaging/completion metadata: native code.
- Schema validation and case completion: native code.

Reserve delegated subagents for steps that genuinely require model reasoning.

### Stage 5 — Durable recovery

The parent runner must be restartable:

- persisted `reconciliation_trigger` is not enough by itself
- startup recovery scans open Kanban-runtime cases
- if a case has requested scheduler handoff or active materialized tasks, rehydrate the parent runner/reconciler
- no process-local-only follow-up loops for accepted architecture

## Acceptance criteria

A clean case should show:

- one parent case runner per case execution
- if a Hermes parent session is used, it has the Kanban toolset for the case-level task
- child step Kanban tasks exist for durable DAG gating/audit, but are non-dispatchable by the global dispatcher
- child step work is delegated from the parent boundary or handled by native deterministic executors
- no full Hermes child session per step unless explicitly model-reasoning-required
- no `hermes chat -q "work kanban task <child_step_task_id>"` logs for Frank child steps
- every child step completion/block has a durable compact Kanban run record
- Step 2 STT latency close to local STT runtime, not 2+ minutes
- deterministic DAG checks before every wave
- Kanban child tasks completed only after cases accepts outputs
- native deterministic runs use `model_backed=false` and skip model audit requirements
- model-backed delegated runs include validated audit records
- cases slots and step status remain authoritative
- Kanban run history remains compact and audit-focused
- restart recovery can resume open cases

## Current live conclusion

Franklin10 is useful evidence, but it is not the final architecture. It proves the control/data-plane reconciliation can work, while exposing that the worker execution boundary is wrong.

Next implementation work should stop adding live-E2E patches to the per-step session model and repair the parent-runner architecture instead.
