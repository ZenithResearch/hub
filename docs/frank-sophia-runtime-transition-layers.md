# Frank/Sophia runtime transition layers

This document describes the narrow runtime transition slice that moves internal case execution ownership away from Sophia and into Frank-owned orchestration. It documents the layers now in play, the boundaries between them, the invariants each layer must preserve, and the defects that were fixed during review.

The design goal is deliberately constrained:

- Frank owns orchestration/runtime control for cases.
- Sophia is comms-only.
- Hermes/Kanban projection is the case-scoped execution contract.
- Zenith/cases remains the source of truth.
- Frank may launch bounded `services.frank.step_runner` subprocesses in this slice.
- This slice does not add Docker isolation, new Hermes instances, federation, secS, or a live Hermes adapter.

## Contents

1. Runtime boundary summary
2. Layer map
3. Source-of-truth model
4. Process contract layer
5. Dispatch packet layer
6. Hermes/Kanban projection layer
7. Workspace policy layer
8. Durable runtime-state layer
9. Step runner launch layer
10. Completion and reconciliation layer
11. Restart/idempotency behavior
12. Sophia boundary
13. Existing worker queue bridge boundary
14. Validation coverage
15. Known limitations and next hardening points
16. File map

---

## 1. Runtime boundary summary

The runtime now treats Frank as the only internal case execution controller. Frank receives the queue message, resolves a known process, creates or reuses the canonical case, compiles the execution packet, projects the case into a case-scoped Kanban contract, launches bounded step runners, and reconciles step results back to the cases service.

Sophia is no longer a dispatch profile or step executor for internal review processing. Sophia remains useful for outbound comms, summaries, public-facing synthesis, and publication/advisory surfaces, but she is not allowed to be the internal case executor.

The important distinction is:

- Frank owns orchestration and runtime control.
- The cases service owns durable state.
- The Kanban projection owns the execution contract shape.
- Step runners do bounded task work and return structured results.
- Sophia owns comms, not internal case mutation.

---

## 2. Layer map

The runtime is organized as a layered path from event to durable reconciliation.

```text
review_submitted event
        |
        v
[Frank dispatcher]
  - resolve sender
  - resolve known process
  - create/reuse case
        |
        v
[Process contract]
  - steps
  - zero-based DAG edges
  - resources/tools/toolsets/skills/env vars
  - dispatch_profile = frank
        |
        v
[Dispatch packet]
  - initial_context
  - resolved_step_briefs
  - capabilities
  - worker_execution_rules
  - hermes_kanban projection
        |
        v
[Hermes/Kanban contract]
  - one board per case
  - one task per case step
  - one link per in-case dependency edge
  - workspace policy per task
        |
        v
[Durable runtime state]
  - cases service step runtime_state_json
  - case logs
  - case status
        |
        v
[Frank step_runner subprocesses]
  - bounded profile/model/toolset execution
  - JSON result artifact
        |
        v
[Reconciliation]
  - output steps -> complete_step_outputs
  - no-output steps -> normal step status COMPLETED
  - failures -> FAILED + case logs
```

The Kanban projection is not decorative metadata. It is the durable case-scoped execution contract Frank emits and persists in the dispatch packet. A future live Hermes adapter can materialize that plan, but the contract exists now and must remain correct even before a live adapter exists.

---

## 3. Source-of-truth model

The runtime separates source-of-truth state from operational execution state.

| Surface | Role | Ownership |
|---|---|---|
| Cases service | Canonical case, step, slot, runtime state, logs, status | Zenith/cases source of truth |
| Process contract | Declared process semantics: steps, dependencies, resources, tools, env | Base ops process docs compiled by cases contract |
| Dispatch packet | Case-scoped compiled execution plan | Frank |
| Hermes/Kanban projection | Operational execution contract for one case board | Frank-owned projection, future Hermes materialization |
| Step runner result artifact | Local subprocess result envelope | Frank runtime scratch, reconciled into cases service |
| Sophia | Human-facing comms/synthesis | Comms-only, no internal case execution |

The cases service remains authoritative. Runtime scratch artifacts and active Python tasks are not authoritative by themselves. They are useful only when paired with durable state in the cases service.

---

## 4. Process contract layer

Primary files:

- `base/ops/processes/process-queued-review.md`
- `base/ops/processes/mock-review-submitted.md`
- `services/cases/contract.py`
- `tests/test_process_contract.py`

The process contract is the declared semantic source for review processing. It now declares Frank as the internal dispatch profile and executor for the review process.

Current review process frontmatter:

```yaml
title: "Process queued review"
doc_type: processes
tags: [review, feedback, audio, transcription, annotation]
dispatch_profile: frank
```

Important contract properties:

- `dispatch_profile: frank` prevents Sophia from becoming the internal execution profile.
- Step executors are `frank` for internal review processing.
- Required capabilities are explicit: skills, tools, toolsets, resources, and env vars.
- `ELEVENLABS_API_KEY` is declared as an environment requirement so Frank can preflight before burning step execution.
- Step 8 uses native `update_review_status`, not a repo/filesystem write.

The process contract also emits DAG edges with zero-based indexes. This is critical for the Kanban projection layer.

Example edge shape:

```json
{"from": 0, "to": 1}
```

Interpretation:

- `from: 0` means the first case step.
- `to: 1` means the second case step.
- Step 0 is a valid parent task and must never be skipped just because it is zero.

---

## 5. Dispatch packet layer

Primary file:

- `services/frank/main.py`

The dispatch packet is Frank's compiled execution packet for a single case. It is persisted on the case record and gives execution a durable, reviewable contract.

Key packet fields:

| Field | Purpose |
|---|---|
| `case_id` | Canonical case identifier |
| `assignment` | Frank-selected executor/profile and assignment metadata |
| `initial_context` | Payload-derived root context values |
| `process_summary` | Contract summary for worker/runner context |
| `step_briefs` | Raw step briefs compiled from contract + case step rows |
| `resolved_step_briefs` | Authoritative per-step execution briefs |
| `dag_edges` | Process DAG edges |
| `capabilities` | Collected skills/tools/toolsets/resources/env vars |
| `worker_execution_rules` | Runtime rules step runners must follow |
| `hermes_kanban` | Case-scoped board/task/link projection |

The assignment now resolves to Frank for internal review cases:

```json
{
  "assignment": {
    "executor": "frank",
    "dispatch_profile": "frank",
    "queue_name": null
  }
}
```

`queue_name: null` is intentional in this slice. Frank direct launch is the active narrow path. The legacy worker queue can exist as compatibility infrastructure, but the docs/tests should not claim it is the active execution path for this slice.

---

## 6. Hermes/Kanban projection layer

Primary files:

- `services/frank/kanban_projection.py`
- `tests/test_frank_kanban_projection.py`

The Kanban projection converts a canonical case and dispatch packet into a case-scoped Hermes materialization plan.

The projection emits:

| Object | Meaning |
|---|---|
| `CaseBoardPlan` | One board scoped to exactly one case |
| `TaskPlan` | One task per canonical case step |
| `TaskLinkPlan` | One dependency link per valid in-case DAG edge |
| `KanbanMaterializationPlan` | Full board/tasks/links/state mapping bundle |

### Board contract

The board is scoped to one case:

```json
{
  "case_id": "case_123",
  "scope": "case",
  "source_of_truth": "Zenith cases service"
}
```

The board lifecycle is defined as:

- created/planned when Frank launches the canonical case
- archived when the canonical case closes
- source of truth remains the cases service
- Hermes task state is operational execution state

### Task contract

Every case step becomes one task. Each task carries canonical identity:

```json
{
  "task_id": "case_123:step_db_1",
  "case_id": "case_123",
  "canonical_step_identity": {
    "step_db_row_id": "step_db_1",
    "step_id": "step_1",
    "idx": 0,
    "name": "Load review record"
  },
  "assigned_profile": "frank",
  "workspace_policy": "scratch",
  "expected_outputs": {}
}
```

The canonical database row id is part of the task id. This makes the task stable across restarts and lets the projection preserve the exact case-step identity from the cases service.

### Link contract

The fixed indexing rule is:

```python
task_id_by_index[int(step_row.get("idx") or 0)] = task_id
```

DAG edges are interpreted against zero-based `idx` values directly. The previous bug used `idx + 1`, then skipped `parent_index == 0`, which broke real edges like `0 -> 1`.

Current behavior:

- `from == to` self-links are skipped.
- `from: 0` is valid and creates a link from the first task.
- unresolved edge endpoints raise `ValueError`.
- unresolved edges are not silently dropped.

Example valid edge:

```json
{"from": 0, "to": 1, "variables": ["review_id"]}
```

Produces:

```json
{
  "parent_task_id": "case_123:step_db_1",
  "child_task_id": "case_123:step_db_2",
  "link_type": "blocks",
  "variables": ["review_id"],
  "source": "canonical_case_dag"
}
```

### Failure policy

If a DAG edge references a missing index, projection raises:

```text
ValueError: unresolved dag edge {'from': 0, 'to': 99, ...}; known step indexes=[0, 1]
```

This is intentional. A missing dependency means the execution contract is malformed and should fail review immediately.

---

## 7. Workspace policy layer

Primary file:

- `services/frank/main.py`

The dispatch path now derives a deterministic `workspace_policy` for each step brief. This fixes the prior gap where the process declared resources but step briefs did not carry a concrete workspace policy into the Kanban projection.

Helper:

```python
def derive_workspace_policy(resources: list[Any], *, case_id: str | None = None) -> str:
    ...
```

Current policy mapping:

| Resource pattern | Workspace policy |
|---|---|
| `review assets workspace`, `review asset` | `dir:/hub/.hermes/frank_execution/{case_id}/assets` |
| `subject codebase`, `hub repo`, `codebase` | `worktree:/hub` |
| `vault`, `publication`, `note synthesis`, `notes workspace`, `daily note` | `scratch:TODO:vault_policy_unresolved` |
| anything else | `scratch` |

The placeholder `{case_id}` is resolved when building the dispatch packet:

```python
_resolve_workspace_policy_case_id(policy, case_id)
```

This means real dispatch packets and projected Kanban tasks carry a non-empty workspace policy. The policy is deliberately conservative where the repo does not yet define a safe vault/publication access mode.

Important boundary:

- Sophia is not granted broad vault/code access by this policy.
- Internal review steps run under Frank for this slice.
- Vault/code access is either explicit (`worktree:/hub`) or marked as scratch/TODO limitation.

---

## 8. Durable runtime-state layer

Primary file:

- `services/frank/main.py`

Frank can launch step runner subprocesses, but launch state cannot live only in `ACTIVE_CASE_TASKS`. `ACTIVE_CASE_TASKS` is process-local memory and disappears on restart.

Before subprocess launch, Frank writes step runtime state to the cases service:

```python
await update_step_runtime_state(client, case_id, step_db_row_id, runtime_state)
```

Runtime state includes:

| Field | Meaning |
|---|---|
| `status` | active/completed/failed runtime state |
| `agent_run_id` | Durable identifier for this runner attempt |
| `wave_id` | Execution wave that launched this step |
| `profile` | Selected execution profile, now Frank for internal review |
| `session_id` | Hermes/runner session id |
| `session_json_path` | Local session artifact path |
| `log_path` | Local subprocess log path |
| `started_at` | Launch timestamp |
| `current_focus` | Step title/focus |
| `tasks` | Runtime task list if the runner records subtasks |
| `retry_count` | Retry accounting |

This is the critical durability boundary. If Frank crashes after writing runtime state but before local in-memory task bookkeeping survives, the cases service still contains evidence that the step was launched.

---

## 9. Step runner launch layer

Primary files:

- `services/frank/main.py`
- `services/frank/step_runner.py`
- `tests/test_frank_step_runner.py`

Frank launches bounded step runners with:

```python
python -m services.frank.step_runner \
  --payload-path ... \
  --result-path ... \
  --session-path ...
```

Launch ordering is important:

1. Build payload paths and runtime metadata.
2. Write payload JSON to local case runtime dir.
3. Write durable runtime state to cases service.
4. Append case log that runner was launched.
5. Spawn subprocess.

The subprocess receives:

| Payload field | Purpose |
|---|---|
| `case_id` | Canonical case id |
| `wave_id` | Wave id |
| `session_id` | Runner session id |
| `profile` | Runtime profile |
| `profile_home` | Hermes profile home |
| `step` | Resolved step brief |
| `slot_values` | Current case slot values |
| `process_summary` | Process-level context |
| `worker_execution_rules` | Runtime behavior contract |
| `allowed_toolsets` | Step/toolset restrictions |
| `max_iterations` | Bounded runner budget |
| model settings | Resolved profile model config |

The runner is intentionally bounded. It does not become the source of truth. It returns a structured result artifact that Frank validates and reconciles.

---

## 10. Completion and reconciliation layer

Primary file:

- `services/frank/main.py`

The review found a defect: `resolve_wave` always called `complete_step_outputs` when a runner succeeded. The cases service rejects `complete-outputs` for steps with no declared `output_variables`.

The corrected rule is:

```text
if declared outputs exist:
    call complete_step_outputs
else:
    mark the step COMPLETED through the normal step status endpoint
```

### Output-producing steps

For steps with declared outputs, Frank calls:

```python
await complete_step_outputs(
    client,
    case_id,
    step_db_row_id,
    outputs,
    agent_run_id=item["agent_run_id"],
    notes=notes,
)
```

This writes output slots and records provenance through the cases service.

### No-output steps

For steps with no declared outputs, Frank calls:

```python
await complete_no_output_step(
    client,
    case_id,
    step_db_row_id,
    agent_run_id=item["agent_run_id"],
    notes=notes,
)
```

`complete_no_output_step` uses:

```text
PUT /cases/{case_id}/steps/{step_db_row_id}
{"status": "COMPLETED"}
```

Runner notes are preserved through case logs, so provenance is not lost just because the step has no output variables.

### Failure path

If the runner fails, returns invalid JSON, omits a result artifact, or reports non-completed status, Frank marks the step failed and appends an error case log.

---

## 11. Restart/idempotency behavior

Primary file:

- `services/frank/main.py`

Frank still uses `ACTIVE_CASE_TASKS` as local process bookkeeping, but it no longer relies on it as the only active execution record.

New helper:

```python
def _case_has_durable_active_steps(case_detail: dict[str, Any]) -> bool:
    ...
```

It checks case step rows for:

- `status in {"RUNNING", "IN_PROGRESS"}`
- `runtime_state_json.status == "active"`

`start_case_execution` now fetches case detail before launching and refuses to duplicate launch if durable active state exists.

Restart behavior now follows this safety rule:

```text
If Frank restarts and ACTIVE_CASE_TASKS is empty, it must inspect cases service state before launching.
If durable active step runtime exists, do not blindly duplicate runners.
```

This does not fully implement crash recovery/reaping for orphaned subprocesses. It does prevent the worst duplicate-launch behavior and makes the current direct runner slice durable enough to survive review.

---

## 12. Sophia boundary

Primary files:

- `rolodex/agents/sophia/SOUL.md`
- `rolodex/agents/sophia/Sophia.md`
- `base/ops/processes/process-queued-review.md`
- `base/ops/processes/mock-review-submitted.md`

Sophia's boundary is now:

- outbound communications
- human-facing summaries
- public/wiki/note synthesis where explicitly routed
- publication-facing advisory work

Sophia is not:

- the dispatch profile for internal case execution
- a step executor for the review process
- the owner of case runtime control
- the actor that mutates internal case state
- the profile that receives broad vault/code access by default

This boundary matters because Sophia's prior descriptions and process defaults implied she could execute internal case work. That contradicted the target architecture.

---

## 13. Existing worker queue bridge boundary

Primary files:

- `services/hermes_worker_queue/main.py`
- `tests/test_hermes_worker_queue.py`
- `rolodex/agents/frank/SOUL.md`

The worker queue bridge still exists. It is not removed. It still has tests. But it is not described as the active execution path for this slice.

Current active path:

```text
Frank -> durable step runtime state -> services.frank.step_runner subprocess -> Frank reconciliation -> cases service
```

Non-goals for this slice:

- no live Hermes adapter
- no new Hermes service
- no per-step Docker isolation
- no federation
- no secS integration

The worker queue tests were updated so they no longer encode Sophia as the expected internal executor. This keeps compatibility tests from reintroducing the old Sophia execution route.

---

## 14. Validation coverage

Focused validation run:

```bash
.venv/bin/python -m unittest \
  tests.test_frank_kanban_projection \
  tests.test_hermes_worker_queue \
  tests.test_frank_dispatcher \
  tests.test_frank_step_runner
```

Result:

```text
Ran 31 tests
OK
```

Additional process-contract validation:

```bash
.venv/bin/python -m unittest tests.test_process_contract -q
```

Result:

```text
Ran 13 tests
OK
```

Compile validation:

```bash
.venv/bin/python -m py_compile \
  services/frank/kanban_projection.py \
  services/frank/main.py \
  services/frank/step_runner.py \
  services/hermes_worker_queue/main.py
```

Result: passed with no output.

Full discovery caveat:

```bash
.venv/bin/python -m unittest discover -s tests
```

Still fails only because local venv lacks pytest:

```text
ModuleNotFoundError: No module named 'pytest'
```

The failing import is `tests/test_vault_write.py`. That is unrelated to this Frank/Sophia runtime slice.

### Specific invariants covered

| Invariant | Test coverage |
|---|---|
| Step 0 is a valid DAG parent | `tests/test_frank_kanban_projection.py` |
| Edge `0 -> 1` creates a task link | `tests/test_frank_kanban_projection.py` |
| Self-links are skipped | `tests/test_frank_kanban_projection.py` |
| Unresolved edges raise `ValueError` | `tests/test_frank_kanban_projection.py` |
| Dispatch packet uses Frank, not Sophia | `tests/test_frank_dispatcher.py` |
| Kanban projection preserves workspace policy | `tests/test_frank_dispatcher.py`, `tests/test_frank_kanban_projection.py` |
| No-output successful steps avoid `complete-outputs` | `tests/test_frank_dispatcher.py` |
| Runner notes survive no-output completion via logs | `tests/test_frank_dispatcher.py` |
| Durable active runtime prevents duplicate launch | `tests/test_frank_dispatcher.py` |
| Worker queue fixtures no longer expect Sophia execution | `tests/test_hermes_worker_queue.py` |
| Process contract parser now expects Frank defaults in fixtures | `tests/test_process_contract.py` |

---

## 15. Known limitations and next hardening points

This slice intentionally stops short of larger architecture work. Remaining hardening points:

1. Orphan detection and recovery

   The restart guard avoids duplicate launch when durable active state exists. It does not yet detect that a previously active subprocess died during a Frank restart. A future slice should add stale runtime detection based on heartbeat/updated_at/log/session evidence.

2. Workspace policy maturity

   Vault/publication/note synthesis currently maps to `scratch:TODO:vault_policy_unresolved` unless a concrete safe policy already exists. This prevents accidental broad internal access but should be replaced with a formal vault workspace policy.

3. Live Kanban materialization

   The Kanban projection is now correct and durable as a contract. It is not yet materialized through a live Hermes adapter. That is intentionally out of scope for this slice.

4. Step 6 behavioral budget

   The previous live run had a component-resolution step that exhausted iteration budget. This slice improves contract/routing/workspace semantics but does not fully redesign that prompt or step strategy.

5. Full unittest discovery dependency

   Full discovery still needs pytest installed or the pytest-based test excluded from unittest discovery.

6. Ad-hoc process fallback

   Frank's docs now reflect the current runtime implementation: known process-backed events are supported. A future narrow fallback can wrap the existing `create-process` convention if ad-hoc runtime process creation becomes required.

---

## 16. File map

| File | Layer | Change |
|---|---|---|
| `services/frank/kanban_projection.py` | Kanban projection | Fixed zero-based edge indexing, self-link skip, unresolved edge error |
| `services/frank/main.py` | Dispatch/runtime/reconciliation | Added workspace policy derivation, no-output completion, durable active-step restart guard |
| `base/ops/processes/process-queued-review.md` | Process contract | Frank dispatch/executor defaults; explicit capabilities and native review status update |
| `base/ops/processes/mock-review-submitted.md` | Process contract fixture | Frank dispatch profile |
| `rolodex/agents/frank/SOUL.md` | Runtime docs | Clarified direct runner slice, no live adapter claim, known-process limitation |
| `rolodex/agents/sophia/SOUL.md` | Agent boundary | Sophia comms-only boundary preserved |
| `rolodex/agents/sophia/Sophia.md` | Agent boundary | Removed stale internal execution framing |
| `rolodex/agents/frank/skills/generate-proc-dag.md` | Skill docs/examples | Removed stale Sophia executor examples |
| `tests/test_frank_kanban_projection.py` | Projection tests | Edge 0 -> 1, self-link skip, unresolved edge raise |
| `tests/test_frank_dispatcher.py` | Runtime tests | Frank assignment, workspace policy, no-output completion, durable active-state guard |
| `tests/test_hermes_worker_queue.py` | Compatibility tests | Non-Sophia worker queue fixtures |
| `tests/test_frank_step_runner.py` | Runner tests | Non-Sophia profile fixture |
| `tests/test_process_contract.py` | Contract parser tests | Frank process/step executor expectations |
| `CHANGELOG.md` | Release notes | Runtime hardening summary under `[Unreleased]` |

---

## Operator summary

The architecture now has the minimum durable runtime shape needed for review:

```text
Frank owns the case runtime.
Cases service owns canonical state.
Kanban projection owns the case-scoped execution contract.
Step runners do bounded work.
Sophia stays comms-only.
```

The key correctness fix was treating the cases contract's DAG indexes as zero-based all the way through projection and launch. The key durability fix was writing step runtime state to the cases service before subprocess launch and checking that durable state after restart before launching again. The key reconciliation fix was separating output-producing step completion from no-output step completion.
