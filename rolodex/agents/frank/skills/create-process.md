---
name: create-process
description: >
  Creates an ad-hoc process definition conforming to the hub Markdown process schema.
  Persisted to base/ops/processes/ with a msg_<id> prefix so it is excluded from
  future Qdrant indexing. Output path is also passed directly to generate-proc-dag
  and POST /cases as process_source.
version: "1.1.0"
---

# create-process

## Purpose

When no existing process matches the request, Frank generates a minimal ad-hoc
process document so the request can still be handled. The process is saved to
`/hub/base/ops/processes/msg_<queue_message_id>-<slug>.md`. The `msg_` prefix marks
it as ad-hoc — the process indexer skips these files, so they never appear in future
semantic search results. If the same type of request recurs repeatedly, a human
author should formalise it into a permanent process (without the `msg_` prefix).

## Process schema reference

Read `/hub/base/ops/processes/` for examples. Match the permanent process schema:
- exhaustive `## Variables` table
- step `**Input:**` and `**Output (process state):**` for slot-backed state only
- step `**Processing:**` instructions for every step
- step `**Skills:**` for one or more skill/worker capabilities being invoked
- step `**Required Resources:**` for non-slot capabilities such as `vault`, `browser`,
  `hub repo`
- optional step `**Suggested Resources:**` for helpful but non-mandatory resources Frank
  may authorize at runtime
- step `**Tools:**` for registry-backed tools

## Inputs

| Name | Description |
|---|---|
| `objective` | One-sentence objective from process-request |
| `event_type` | Queue message event type |
| `message_body` | Original message body, for inferring steps |
| `payload` | Message payload fields |

## Steps

### Step 1 — Infer root variables

From the event_type and payload, identify the named variables available at process start.
These become `## Variables` rows and root `**Input:**` references.

### Step 2 — Infer process steps

Based on the objective, decompose the work into 2–5 ordered steps. For each step:
- Assign a step number and title
- Write concise `**Processing:**` prose
- Declare `**Input:**` variables: only slot-backed process state
- Declare `**Output (process state):**` variables only when the step produces new
  process-state values that downstream steps consume
- Declare `**Required Resources:**` entries for the external surfaces or locations the
  step must have
- Declare `**Suggested Resources:**` only when a non-mandatory resource would help but
  the step can still run without it
- Declare `**Tools:**` entries for concrete registry-backed tools
- Declare `**Skills:**` for the primary skill/worker invocation(s)

Keep steps atomic — each should be a single skill invocation.

Do **not** model `vault`, `hub repo`, browser access, or similar side effects as
`**Output (...)**` labels. Those are resources plus processing prose.

### Step 3 — Construct and save the process document

Construct the Markdown document matching the permanent process schema. Save it to:

```
/hub/base/ops/processes/msg_<queue_message_id>-<event_type_slug>.md
```

where `<event_type_slug>` is the `event_type` lowercased with underscores replaced by
hyphens (e.g. `review_submitted` → `review-submitted`).

The `msg_` prefix is required — it is the indexer's signal to exclude this file from
Qdrant. Do not omit it.

~~~markdown
# Ad-hoc: <objective summary>

## What this process does

<objective>

---

## Steps

### Step 1 — <step title>

**Skills:** `<skill-name>`

**Input:** `<root_input>`

**Required Resources:** `vault`

**Tools:** `echo_tool`

**Processing:** <what happens here>

**Output (process state):**
```json
{
  "derived_value": "..."
}
```

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `<root_input>` | string | Root value available from the queue payload |
| `derived_value` | string | Process-state value produced by Step 1 |
~~~

## Output

The saved file path (e.g. `/hub/base/ops/processes/msg_abc123-review-submitted.md`).
Pass the file path as `process_path` and read the file content as `process_source`
when calling `generate-proc-dag` and `POST /cases`.

## Notes

- Flag the case as using an ad-hoc process in the case logs: type `SYSTEM_EVENT`,
  message "Ad-hoc process created — no permanent process matched this request type."
- If the same ad-hoc structure is generated three or more times, log an observation
  that a permanent process should be authored.
