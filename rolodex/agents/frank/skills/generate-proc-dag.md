---
name: generate-proc-dag
description: >
  Parses a process definition's step process-state I/O declarations and derives
  the execution DAG. Returns an ordered list of parallel groups — sets of steps
  that can run concurrently. Steps with no unresolved variable dependencies form
  the first group.
version: "1.0.0"
---

# generate-proc-dag

## Purpose

Frank does not improvise execution order. The DAG is derived deterministically
from the process's declared process-state step I/O. This skill does that derivation.

A step's inputs fall into two categories:
- **Process inputs** (`from: "process.<name>"`): available immediately from the
  message payload — no dependency on any step output.
- **Step outputs** (`from: "step_N.<output_name>"`): available only after step N
  has completed — this creates a directed edge N → current step.

`Required Resources:`, `Suggested Resources:`, and `Tools:` declarations are metadata
only. They do not create DAG edges.

`Executor:` is the worker/profile target for dispatch.
`Skill:` contributes the step action/instructions, but it is not the dispatch target.

Steps are grouped into **parallel groups** (idx values in case_steps). All steps
in a group are dispatchable at the same time. A group is released only when all
steps in the previous group are complete.

## Inputs

| Name | Description |
|---|---|
| `process_source` | Markdown process snapshot (from match-process or create-process) |
| `available_inputs` | Dict of slot names already available from the message payload |

## Algorithm

```
1. Parse the process document:
   - build the authoritative `## Variables` set
   - for each step, collect `Input`, `Output (process state)`, `Executor`,
     `Required Resources`, `Suggested Resources`, `Tools`, `Skills`

2. Build dependency graph from variables only:
   - producer_map[variable] = step that emits it via `Output (process state)`
   - for each step input variable:
       - if it has a producer step, add edge producer → current step
       - otherwise it is a root input available at case start

3. Topological sort using Kahn's algorithm:
   - L = [] (result)
   - queue = all steps with in-degree 0 (no upstream dependencies)
   - while queue is not empty:
       group = all steps currently in queue  (these run in parallel)
       L.append(group)
       for each step S in group:
         for each step T that depends on S:
           decrement T's in-degree
           if T's in-degree == 0: add T to queue

4. Assign idx:
   each group in L gets an idx starting at 0
   steps within a group all share the same idx

5. Validate:
   if any steps remain unprocessed after the loop → cycle detected → raise error
```

## Outputs

```json
{
  "groups": [
    {
      "idx": 0,
      "steps": [
        {
          "step_id": "step_1",
          "name": "Load review record",
          "executor": "frank",
          "action": "load-review",
          "inputs": { "review_id": "<from process input>" },
          "outputs": ["audio_asset_path", "events"],
          "resources": [],
          "tools": []
        }
      ]
    },
    {
      "idx": 1,
      "steps": [
        {
          "step_id": "step_2",
          "name": "Transcribe audio",
          "executor": "frank",
          "action": "transcribe-review-audio",
          "inputs": { "audio_asset_path": "step_1.audio_asset_path" },
          "outputs": ["transcript", "words"],
          "resources": ["vault"],
          "tools": ["echo_tool"]
        },
        {
          "step_id": "step_3",
          "name": "Validate events",
          "executor": "frank",
          "action": "validate-events",
          "inputs": { "events": "step_1.events" },
          "outputs": ["validated_events"]
        }
      ]
    }
  ],
  "total_steps": 3,
  "has_parallel_groups": true
}
```

## Error handling

- Cycle detected: return `{ "error": "cycle", "cycle_steps": ["step_2", "step_4"] }`.
  Caller should log and fall back to sequential execution order.
- Step references non-existent upstream step: return
  `{ "error": "dangling_ref", "step": "<id>", "references": "<missing_step_id>" }`.
