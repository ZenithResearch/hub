---
title: Processes
type: moc
description: "MOC for hub process definitions — declarative SOPs that Frank RAGs to map incoming events to execution workflows"
---

# Processes

Process definitions live here. Each file is a declarative SOP in Markdown. Frank
searches this directory during Phase 1 intent recognition to find the right process
for an incoming event.

## What a process file looks like

```markdown
---
title: "Service request intake"
doc_type: processes
tags: [intake, dispatch]
---

# Service request intake

## Trigger
...

## Steps
...

## Outputs
...
```

The `doc_type` field is optional — the indexer derives it from the directory name.
`title` and `tags` are the most useful fields for search precision.

## Variables are authoritative

Every process doc must declare an exhaustive `## Variables` table. That table is the
authoritative slot manifest for every case created from the process.

- Every `## Variables` row becomes a case slot.
- Step `**Input:**` and process-state `**Output (process state):**` keys may reference
  only names declared in `## Variables`.
- Variable descriptions and types are required — they power runtime validation and the
  ZenithOS UI.
- Every step must declare explicit `**Processing:**` instructions.
- `**Skills:**` declares one or more skill/worker capabilities the step invokes.
- `**Required Resources:**` declares the non-slot capabilities or surfaces a step needs
  in order to run, such as `vault`, `hub repo`, `browser`, or other workspace/system
  surfaces.
- `**Suggested Resources:**` declares useful but non-mandatory resources Frank may
  permit at runtime if they improve execution. Resource declarations are not an
  allowlist; Frank may authorize additional resources at his own discretion.
- `**Tools:**` is the authoritative place to declare registry-backed tools a step needs.
- `**Output (vault):**`, `**Output (hub repo):**`, and similar non-process-state output
  mediums are no longer valid process schema. Express those writes in `**Processing:**`
  prose and declare the relevant step resource instead.
- Resources and tools are always declared on the step that uses them, never once as a
  global process-level capability block.
- Resources and tools are never declared in `## Variables` and never become slots.

## Naming convention

`{verb}-{noun}.md` — e.g. `handle-service-request.md`, `dispatch-cleaning-job.md`

## Index

- [process-queued-review](process-queued-review.md) — Transcribe audio, reconstruct narrative, extract reviewer-voice observations, and write the vault review artifacts. One review per invocation.
