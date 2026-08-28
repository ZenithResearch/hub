---
name: step-execution-loop
description: >
  Frank-owned inner loop contract for bounded Hermes/profile workers executing a
  single assigned step inside a native case pipeline.
version: "1.2.0"
---

# step-execution-loop

## Purpose

Execute one assigned step inside a Frank-compiled case and return a
structured JSON result. Frank owns canonical case writeback, lifecycle decisions,
retries, reroutes, and reconciliation. The worker owns only the bounded task work
inside the case execution contract.

## Inputs

- `case_id`
- `step_db_row_id`
- `agent_run_id`
- assigned profile / seat
- workspace policy
- step-local memo
- resolved step brief, including declared output names and types
- current input slot values supplied by Frank/the parent runtime

## Loop

1. Read the assigned task and resolved step brief.
2. Confirm required inputs are present. If required inputs are missing, return a
   failed JSON envelope immediately; do not search unrelated directories or infer
   missing case state.
3. Expand the step into a local task list if needed.
4. Persist current focus/progress with the runtime-state mechanism exposed by the
   parent execution environment.
5. Execute the bounded task work in the assigned workspace policy scope.
6. Merge local task results into the step memo.
7. Return a structured JSON envelope with either:
   - `status = "completed"` and `outputs = {...}` matching the declared output schema
   - or `status = "failed"` and a concise `reason`
8. Add logs/notes for meaningful progress or findings when the runtime exposes a
   logging surface.
9. If the step has no declared outputs, say so explicitly in the returned result
   so Frank can decide whether the verified side effect is enough to complete it.
10. Do not durably complete the step from the child; return the result to Frank.

## Review Asset Steps

If the step brief includes review asset IDs or instructs you to work from review
audio/events, use the materialized asset paths supplied in the task context. Do
not assume assets already exist under `/hub/data/reviews/assets`.

## Rules

- The authoritative result is the final JSON envelope returned to Frank.
- For successful output-producing steps, `outputs` must contain exactly the
  declared output variable names.
- Output values must match the declared variable types in the resolved step brief.
- Do not call `set_step_running`; execution ownership is established by Frank and
  the Cases step-run lifecycle.
- Runtime state is mutable task tracking only; it is not a substitute for
  declared outputs.
- Task helpers may not delegate further unless Frank explicitly assigned an
  orchestrator task.
- Do not redefine the case DAG or invent new slot names.
- Do not write case slots directly from the child worker.
- Do not claim Sophia responsibilities; Sophia is comms-only and not the internal
  case execution owner.
