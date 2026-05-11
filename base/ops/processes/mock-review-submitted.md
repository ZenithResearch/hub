---
title: "Process queued review"
doc_type: processes
tags: [review, feedback, audio, transcription, annotation, mock]
dispatch_profile: frank
---

> **MOCK — DELETE WHEN DONE.** This is a smoke-test version of `process-queued-review`.
> Same nine-step structure, same key inputs/outputs. Every step echoes fixed values.
> No tools, no API calls, no file I/O. Maximum 40 tokens per step.

# Process queued review

## What this process does

Turns a raw mock review submission into a processed review document placeholder for runtime smoke testing.

---

## What Frank provides

Frank invokes this process when a `mock_review_submitted` event fires. The worker receives:

```json
{
  "review_id": "mock-0000-0000-0000-000000000001",
  "subject_id": "http://localhost:3000/?reviewMode=on",
  "submitted_by": "Gabriel",
  "reviewed_at": "2026-04-23T00:00:00Z",
  "duration_ms": 10000,
  "audio_asset_id": "mock-audio-asset-id",
  "events_asset_id": "mock-events-asset-id"
}
```

---

## Steps

### Step 1 — Load review record

**Executor:** `frank`
**Assignee:** `frank`
**Input:** `review_id`, `audio_asset_id`, `events_asset_id`, `subject_id`, `submitted_by`, `reviewed_at`, `duration_ms`
**Processing:** Print `.` and produce mock loaded review state.
**Output (process state):**
```json
{
  "review_id_short": "mock-000",
  "audio_asset_path": "mock.webm",
  "events": []
}
```

---

### Step 2 — Transcribe audio

**Executor:** `frank`
**Assignee:** `worker`
**Input:** `audio_asset_path`, `events`
**Processing:** Print `..` and produce a mock transcript.
**Output (process state):**
```json
{
  "transcript": "mock transcript",
  "audio_offset_ms": 0,
  "words": []
}
```

---

### Step 3 — Resolve component names

**Executor:** `frank`
**Assignee:** `worker`
**Input:** `subject_id`, `events`
**Processing:** Print `...` and produce a mock unresolved component target.
**Output (process state):**
```json
{
  "component_names": []
}
```

---

### Step 4 — Annotate transcript

**Executor:** `frank`
**Assignee:** `worker`
**Input:** `transcript`, `words`, `audio_offset_ms`, `events`, `component_names`, `review_id`, `submitted_by`, `reviewed_at`, `subject_id`, `audio_asset_id`
**Processing:** Print `....` and produce a mock resolved transcript plus transcript note path.
**Output (process state):**
```json
{
  "transcript_note_path": "mock-transcript.md",
  "resolved_transcript": "mock transcript"
}
```

---

### Step 5 — Extract observations

**Executor:** `frank`
**Assignee:** `worker`
**Input:** `resolved_transcript`, `words`, `events`, `component_names`
**Processing:** Print `.....` and produce mock observation state.
**Output (process state):**
```json
{
  "observations": [],
  "silent_annotations": [],
  "filtered_points": []
}
```

---

### Step 6 — Bind feedback to codebase context

**Executor:** `frank`
**Assignee:** `worker`
**Input:** `observations`, `silent_annotations`, `filtered_points`, `resolved_transcript`, `component_names`, `events`, `subject_id`
**Processing:** Print `......` and produce mock codebase context.
**Output (process state):**
```json
{
  "codebase_context": []
}
```

---

### Step 7 — Write review document

**Executor:** `frank`
**Assignee:** `worker`
**Input:** `observations`, `silent_annotations`, `resolved_transcript`, `component_names`, `codebase_context`, `review_id`, `subject_id`, `submitted_by`, `reviewed_at`, `duration_ms`, `transcript_note_path`
**Processing:** Print `.......` and produce a mock review note path.
**Output (process state):**
```json
{
  "review_note_path": "mock-review.md"
}
```

---

### Step 8 — Update review status

**Executor:** `frank`
**Assignee:** `worker`
**Input:** `review_id`, `review_note_path`
**Processing:** Print `........` and produce a mock status update confirmation.
**Output (process state):**
```json
{
  "review_status_updated": {
    "review_id": "mock-0000-0000-0000-000000000001",
    "status": "processed",
    "review_note_path": "mock-review.md"
  }
}
```

---

### Step 9 — Log in daily note

**Executor:** `frank`
**Assignee:** `worker`
**Input:** `review_id_short`, `subject_id`, `transcript_note_path`, `review_note_path`, `observations`, `review_id`, `review_status_updated`
**Processing:** Print `.........` and do nothing else.

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `review_id` | string (UUID) | Unique identifier for the review submission |
| `audio_asset_id` | string (UUID) | Asset ID of the mock audio recording |
| `events_asset_id` | string (UUID) | Asset ID of the mock events JSON file |
| `subject_id` | string (URL) | URL of the subject being reviewed |
| `submitted_by` | string | Name of the reviewer who submitted |
| `reviewed_at` | string (ISO 8601) | Timestamp when the review was submitted |
| `duration_ms` | number | Duration of the audio recording in milliseconds |
| `audio_asset_path` | string (path) | Filesystem path to the mock audio recording |
| `events` | array | Interaction events from the review session |
| `transcript` | string | Mock transcript |
| `audio_offset_ms` | number | Mock audio offset |
| `words` | array | Mock word timestamps |
| `component_names` | array | Mock component names |
| `transcript_note_path` | string (path) | Mock transcript note path |
| `resolved_transcript` | string | Mock resolved transcript |
| `observations` | array | Mock observations |
| `silent_annotations` | array | Mock silent annotations |
| `filtered_points` | array | Mock filtered points |
| `codebase_context` | array | Mock codebase context |
| `review_id_short` | string | Short review identifier |
| `review_note_path` | string (path) | Mock review note path |
| `review_status_updated` | object | Mock status update confirmation |
