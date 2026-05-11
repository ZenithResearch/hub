---
name: review-submission-processing
category: workflow
description: Standard workflow for processing review submissions from the review_sdk queue
created: 2026-04-24
---

# Review Submission Processing Workflow

> Deprecated as an active Frank runtime path. The live flow is now:
> deterministic Frank case creation + case-scoped dispatch packet + native Frank
> case pipeline execution that writes a canonical `review_packet.json` before
> rendering the human review document.

## Overview
Processes review submissions arriving via the workspace queue with event_type `review_submitted` from source `review_sdk`.

## Operating Loop

### 1. Identify Sender
- Extract `sender` field from queue message
- Extract `review_id` from `message_body` and `payload`
- Verify `source_type` = `review_sdk`

### 2. Determine Intent
Queue message contains a completed review session with:
- Review metadata (timestamps, duration, user)
- Asset references (events JSON + audio recording)
- Interaction data (stroke count, event count)
- Current status: "queued"

**Intent**: Validate, store, and mark review as completed.

### 3. Match Process
Use `/hub/base/ops/processes/process-queued-review.md` as the source of truth. Current live behavior is the native Frank case pipeline:
1. load/materialize review assets
2. transcribe audio
3. resolve component candidates from concrete `event.target` values
4. annotate transcript/segments
5. build `review_packet.json` from normalized events, per-event `target_events`, segments, feedback items, `actionability`, `negative_evidence`, and `implementation_handoff`
6. bind codebase context per feedback item or emit explicit deferred/blocked source-binding records
7. render the markdown review from the packet
8. update review status with `review_packet_status`
9. log the completed review in the daily note

Local-run finding: repeated targets must be aligned through individual target events, never aggregate target spans. Aggregate spans caused false attachment of repeated targets to unrelated transcript segments.

### 4. Create Case
Register the case with the cases service. POST to `http://cases:8083/cases`:

```json
{
  "queue_message_id": "<queue_message_id>",
  "process_name": "review_submitted",
  "process_path": "base/ops/processes/process-queued-review",
  "process_source": "<full contents of /hub/base/ops/processes/process-queued-review.md>",
  "title": "review_submitted from <sender>",
  "objective": "<review_id>",
  "sender": "<sender>"
}
```

The `process_source` field must be the full Markdown content of the process file —
read it with `read_file /hub/base/ops/processes/process-queued-review.md`.
The cases service compiles the contract from this source and will reject anything else.

The response contains `case_id` — use this for all subsequent case operations and logs.
Do not invent a local case ID format.

### 5. Dispatch to Worker
Execute workflow with:

#### Step 1: Validate Payload
- Verify all required fields present
- Check duration matches timestamps
- Validate asset count
- Confirm status is "queued"

#### Step 2: Verify Assets
For each asset in payload.assets:
- Verify asset_id presence
- Check mime_type matches asset_type
- Validate size_bytes > 0
- Mark verification status

#### Step 3: Store Review Data
Create directory: `/app/reviews/{review_id}/`
Files:
- `review.json` - Complete review record with all metadata
- `assets.json` - Asset verification details
- Status field: queued → processing

#### Step 4: Update Status
- Update status: processing → completed
- Add processing_completed_at timestamp
- Record processing_result (success/warnings/errors)
- Persist to review.json

#### Step 5: Generate Summary
Create `summary.json` with:
- Duration analysis
- Interaction metrics (strokes, events, rates)
- Asset summary
- Quality indicators

## Payload Structure
```json
{
  "queue_message_id": "string",
  "event_type": "review_submitted",
  "source_type": "review_sdk",
  "sender": "string",
  "message_body": "review_id",
  "payload": {
    "review_id": "uuid",
    "subject_id": "url",
    "submitted_by": "string",
    "started_at": "ISO8601",
    "stopped_at": "ISO8601",
    "duration_ms": "integer",
    "asset_ids": ["uuid", ...],
    "metadata": {"stroke_count": int, "event_count": int},
    "assets": [{
      "asset_id": "uuid",
      "asset_type": "events|audio",
      "mime_type": "string",
      "size_bytes": integer,
      "created_at": "ISO8601"
    }],
    "status": "queued",
    "created_at": "ISO8601"
  }
}
```

## Output Structure
```
case runtime artifacts/
├── transcript.json              # STT output / normalized transcript payload
├── transcript_{review_id_short}.md
├── review_packet.json           # Canonical packet used by downstream automation
└── review_{review_id_short}.md  # Human handoff rendered from the packet
```

`review_packet.json` must include source asset pointers, normalized `target_events`, transcript segments, feedback items/observations, per-feedback source bindings, actionability buckets, negative evidence, implementation handoff tasks, and a packet quality/status field. The gateway review status should record the generated review note and packet status. Downstream implementation agents consume `implementation_handoff.implementation_tasks`; they should not reconstruct tasks from markdown.

## Success Criteria
- All validation checks pass
- All assets verified/materialized
- `review_packet.json` exists and reports `review_packet_ready` or an explicit degraded status
- `review_packet.json` includes `actionability`, `negative_evidence`, and `implementation_handoff`
- `target_events` are per-event, not aggregate target spans
- Source binding is represented per feedback item as verified/deferred/blocked; empty binding cannot look successful
- Review markdown is rendered from the packet
- Review status = `processed` only when ready, or records degraded packet status/reason otherwise
