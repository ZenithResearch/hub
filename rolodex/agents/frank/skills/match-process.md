---
name: match-process
description: >
  Searches base/ops/processes/ for the process that best matches a given intent
  and event type. Uses keyword search. Returns the process file path and
  parsed definition, or null if no match.
version: "1.0.0"
---

# match-process

## Purpose

Given an objective statement and event_type, find the most appropriate process
definition in `/hub/base/ops/processes/`. Returns the process path and content on
match, or null if no suitable process exists.

## Inputs

| Name | Description |
|---|---|
| `objective` | One-sentence objective statement from process-request Step 2 |
| `event_type` | Queue message event type (e.g. `review_submitted`) |

## Steps

### Step 1 — Direct event_type lookup

Check `/hub/inbox/types/index.yaml`. If the event_type has an associated process or
template hint, treat it as a strong candidate, but still return through the normal
match result shape used by the rest of Frank's queue loop.

### Step 2 — Keyword search over process index

Read `/hub/base/ops/processes/index.md` for the list of available processes and their
one-line descriptions. Score each description against the objective using keyword
overlap. Candidates with any overlap proceed to Step 3.

### Step 3 — Full-text match on candidate processes

For candidates from Step 2, read each process definition file. Match the objective
against the process `description` and `steps[].description` fields. Select the
highest-scoring match.

Scoring heuristic (apply in order, stop at first strong match):
1. Exact event_type match in process frontmatter tags → score 1.0
2. Objective contains ≥3 words from process description → score 0.8
3. Objective shares domain/arena with process frontmatter → score 0.5
4. Partial word overlap only → score 0.2

### Step 4 — Threshold decision

- Score ≥ 0.5: return the process path + parsed YAML frontmatter
- Score < 0.5: return null (caller should invoke create-process)

## Output

```json
{
  "matched": true,
  "process_path": "base/ops/processes/process-queued-review.md",
  "process_name": "Process queued review",
  "process": { }
}
```
or `{ "matched": false }`.

## Notes

- Do not modify process files. This skill is read-only.
- Future: integrate with KB search (`SearchKnowledge` gRPC) for semantic matching.
  For MVP, file-based search suffices given the small number of processes.
