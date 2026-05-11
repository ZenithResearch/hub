---
name: process-request
description: >
  Full intake pipeline for a dequeued message. Establishes sender context,
  identifies intent, matches or creates a process, generates the DAG, creates
  a contract-backed case, persists a dispatch packet, and enqueues a durable
  worker assignment for the case.
version: "1.0.0"
---

# process-request

## Purpose

This is Frank's primary skill — the entry point for every queue message. It chains
the other skills in sequence and produces a dispatched case. Call this skill once per
dequeued message, with the full message JSON as context.

## Inputs

| Name | Source | Description |
|---|---|---|
| `queue_message_id` | message field | ID of the queue message being processed |
| `event_type` | message field | Semantic event label (e.g. `review_submitted`, `service_request`) |
| `source_type` | message field | Channel: `email`, `matrix`, `api`, `webhook`, `manual`, `internal` |
| `sender` | message field | Sender identifier (Matrix ID, email, etc.) |
| `message_body` | message field | Free text content of the request |
| `payload` | message field | Structured extras from the producer (attachments, ids, etc.) |

## Steps

### Step 1 — Establish sender context

Look up the sender in `/hub/rolodex/`. Check `rolodex/index.yaml` for the registry,
then read the matching agent or person entry.

- If the sender is **known**: read their rolodex entry for trust level, role, and context.
- If the sender is **unknown**: create a trust acknowledgement log entry and proceed
  with `trust: unverified`. Do not halt processing for operator-level requests.

Write the resolved sender context to a local variable for use in subsequent steps.

### Step 2 — Determine intent

Read `message_body` and `payload`. Produce a one-sentence objective statement:
> "The sender wants to [action] [subject]."

If intent cannot be determined from the message alone, generate a single clarifying
question and pause. Do not proceed to Step 3 until intent is confirmed.

### Step 3 — Load event type template (if applicable)

Check `/hub/inbox/types/index.yaml` for the `event_type`. If a matching entry exists,
load its `template.md` — this contains handling context specific to this event type.
If the type is not registered, fall back to the `service_request` default
at `/hub/inbox/types/message/template.md`.

### Step 4 — Match or create a process

Call the `match-process` skill with the objective statement and event_type.

- If a match is found: load the exact Markdown process snapshot from `base/ops/processes/`.
- If no match is found: call the `create-process` skill to build a temporary
  Markdown process snapshot in memory. Do not write it to disk — it is session-scoped only.

### Step 5 — Generate the DAG

Call `generate-proc-dag` with the process snapshot. Derive execution order from
process-state inputs/outputs only:
- variable dependencies create DAG edges
- `Resource:` and `Tool:` declarations are metadata/capabilities only and do not
  create dependency edges

Receive back:
- an ordered list of steps with their slot-backed input/output declarations
- each step's explicit `Executor:` target, when declared
- each step's `Skill:`/action guidance, distinct from the worker target
- parallel groups: sets of steps whose variable inputs are already available

### Step 6 — Create the case

POST to `http://cases:8083/cases` with:
```json
{
  "queue_message_id": "<id>",
  "process_name": "<name or 'adhoc'>",
  "process_path": "<path or null>",
  "process_source": "<exact markdown snapshot>",
  "title": "<one-line summary of objective>",
  "objective": "<full objective statement>",
  "sender": "<sender id>"
}
```

The cases service compiles the process contract from `process_source`, precreates
the case steps, and precreates the full slot set from `## Variables`. Do not POST
step definitions separately for contract-backed cases.

### Step 7 — Persist dispatch packet

Persist a case-scoped execution packet containing:
- `case_id`
- sender identity and trust
- objective statement
- resolved worker target / Hermes profile
- process summary
- resolved step briefs compiled immediately after dequeue and process match
- worker execution rules
- payload-derived initial context the worker must materialize into slots

The packet is the durable handoff contract. Frank does not write any slots.

### Step 8 — Dispatch case to worker

Enqueue a worker-assignment message onto the shared worker queue using the
resolved `dispatch_profile`. The worker queue consumer claims that assignment,
launches Hermes with the selected profile or the default profile when none was
resolved, reads the dispatch packet, writes the initial payload-derived slots
through the case toolset, and then begins normal step execution.

The worker must treat `resolved_step_briefs` as the authoritative step brief for
the run. Those briefs preserve the process DAG and declared I/O, while allowing
Frank to normalize or infer instructions when the process text is weak or missing.

## Output

A dispatched case. The case_id is returned and logged. The queue message remains
in queue-processing until durable handoff is complete: case exists, dispatch packet
exists, and the worker assignment is queued successfully. Case completion is a separate lifecycle
tracked by the cases service.

## Error handling

- If the cases service is unreachable: nack the queue message with `force_dlq=false`
  (it will retry). Do not proceed to dispatch.
- If process matching fails entirely: log to cases and set case status to FAILED.
  Nack with `force_dlq=false`.
