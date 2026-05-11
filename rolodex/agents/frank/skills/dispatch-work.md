---
name: dispatch-work
description: >
  Dispatches a case to a worker agent. Frank owns case creation, packet
  persistence, and case-scoped audit writes; the worker owns initial slot
  materialization and step execution.
version: "1.0.0"
---

# dispatch-work

## Purpose

Takes a case_id plus a durable execution packet and sends the case to the
declared executor agent. Worker agents are reachable within the Docker network.

## Inputs

| Name | Description |
|---|---|
| `case_id` | The case to which these steps belong |
| `dispatch_packet` | Structured case-scoped packet persisted on the case |

## Step-by-step

### Step 1 — Identify executor

Read the worker target from the case-scoped dispatch packet.

Frank resolves and stores:
- `assignment.executor`
- `assignment.dispatch_profile`
- `assignment.profile_resolution`

If no profile is resolved, the worker consumer falls back to the default
Hermes profile.

### Step 2 — Persist execution packet

Persist the execution packet on the case before launch. The packet should include:
- sender identity / trust
- objective
- process identity
- worker target
- process summary
- raw `step_briefs`
- authoritative `resolved_step_briefs`
- worker execution rules
- payload-derived initial context the worker must materialize into slots

After the initial `POST /cases`, Frank must never create another case for this
trigger. There is no such thing as a dispatch-log case.

### Step 3 — Enqueue worker assignment

Enqueue a durable worker-assignment message onto the shared worker queue. The
shared Hermes worker consumer claims that assignment and launches Hermes with
the resolved profile.

**Dispatch Contract:**
- `case_id`
- `assignment_id`
- `dispatch_profile`
- `executor`

This is fire-and-forget after worker-assignment enqueue succeeds. Frank does not wait
for worker completion before settling the original queue trigger.

The worker updates steps, slots, runtime state, and logs in the cases service
directly as it executes. Its first action is to read the dispatch packet, write
the initial payload-derived slots through the case toolset, and then allow the
cases service to derive step readiness/completion from durable slot state.

The worker should:
- re-check readiness from live slot population before each spawn wave
- spawn all runnable assigned steps in parallel
- persist per-step task/runtime state while those step runs are active
- avoid `set_step_running`; output slots or explicit no-output completion are
  the durable completion signals

Required dispatch audit logs belong on the existing case only:
- case created
- worker assignment enqueued
- worker wake published or failed

## Output

Durable worker assignment plus a case-scoped execution contract that the worker
can execute without rediscovering the process in the repo.
