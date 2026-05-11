---
name: dispatch-review-submission
category: workflow
description: Dispatch review submission events through the processing pipeline with proper cases service API usage
created: 2026-04-26
---

# Dispatch Review Submission Workflow

> Deprecated for runtime dispatch. Keep this only as historical/reference material.
> Live queue handling now uses Frank's deterministic case-dispatch path plus a
> case-scoped dispatch packet and native Frank case pipeline. The local run showed
> review-packet boundary is the reliable handoff: build `review_packet.json`,
> align transcript segments using individual `target_events`, classify actionability,
> preserve negative evidence, create `implementation_handoff.implementation_tasks`,
> then render markdown and update review status from that packet.

## Overview
Historically processed `review_submitted` events from the workspace queue by creating
a case with the "Process queued review" workflow and then populating required slots
directly in Frank.

## Prerequisites
- Cases service available at `http://cases:8083`
- Process markdown document available at `/hub/base/ops/processes/process-queued-review.md`
- Review payload with `review_id`, `asset_ids`, `subject_id`, `submitted_by`, timestamps

---

## Operating Procedure

### Step 1: Load the Process Document

**Action:** Read the complete process markdown from `/hub/base/ops/processes/process-queued-review.md`

**Critical:** The `process_source` field MUST include the full markdown document with the `---` YAML frontmatter header. This includes:
- Title, doc_type, tags
- All sections (When to use, What this process does, What Frank provides, Steps, Variables, Artifacts)
- The `## Variables` table is REQUIRED (process validation fails without it)

### Step 2: Create Case Once

**Endpoint:** `POST http://cases:8083/cases`

**Required Fields:**
```json
{
  "queue_message_id": "msg-uuid-from-workspace-queue",
  "process_source": "<FULL PROCESS MARKDOWN DOCUMENT>",
  "title": "review_submitted from <sender>",
  "objective": "<review_id>",
  "sender": "<sender_username>"
}
```

**Optional Fields:**
- `process_name`: "review_submitted"
- `process_path`: "base/ops/processes/process-queued-review"

**Response:** Returns `{"case_id": "case-uuid"}`

**Critical:** This is the only `POST /cases` in the entire dispatch path. After the
real review case exists, every durable write must target that `case_id`.

### Step 3: Populate Slots

**Endpoint:** `POST http://cases:8083/cases/<case_id>/slots`

**Critical:** Slots MUST be populated **one at a time**, not in batch. Each request is a single `{name, value}` object.

**Required Slots from Review Payload:**
```python
payload = {
    "review_id": "uuid",
    "asset_ids": ["events_uuid", "audio_uuid"],  # [0] = events, [1] = audio
    "subject_id": "http://...",
    "submitted_by": "username",
    "started_at": "2026-04-25T21:22:49.360Z",
    "stopped_at": "2026-04-25T21:23:02.322Z",
    "duration_ms": 12962,
    "metadata": {"stroke_count": 3, "event_count": 232}
}
```

```python
slots = [
    {"name": "review_id", "value": payload["review_id"]},
    {"name": "audio_asset_id", "value": payload["asset_ids"][1]},  # audio is second
    {"name": "events_asset_id", "value": payload["asset_ids"][0]},  # events is first
    {"name": "subject_id", "value": payload["subject_id"]},
    {"name": "submitted_by", "value": payload["submitted_by"]},
    {"name": "reviewed_at", "value": payload["stopped_at"]},
    {"name": "duration_ms", "value": payload["duration_ms"]},
    {"name": "audio_asset_path", "value": f"data/reviews/assets/{payload['asset_ids'][1]}"}
]
```

**For each slot:**
```python
for slot in slots:
    req = POST /cases/<case_id>/slots
    body: {"name": slot["name"], "value": slot["value"]}
```

### Step 4: Fetch Live Case and Select First Wave

**Critical:** Step IDs are UUIDs, NOT sequential numbers. You MUST fetch the case first to get the actual step IDs.

**Step 4a: Fetch Case Details**

```python
response = GET /cases/<case_id>
# Extract step IDs from response['steps'] (top-level array, NOT response['case']['steps'])
# Each step has: {"id": "step_uuid_here", "step_id": "step_1", "name": "..."}
```

**Step 4b: Select Only the Dispatchable First Wave**

Use the live `response['steps']` array as the source of truth. A step is
dispatchable only if its persisted status is `READY`.

If no steps are `READY`, record a case-scoped dispatch log and stop. Do not
invent a successful dispatch narrative.

### Step 5: Launch Worker for the Existing Case

Worker launch must use the existing `case_id`. Never create a new case for
dispatch logging, retries, or worker bookkeeping.

All dispatch audit events belong on:
`POST /cases/<case_id>/logs`

Never use `POST /cases` as a generic durable-write or logging endpoint.

### Step 6: Mark Dispatched Steps RUNNING

Only after the worker launch is accepted, update the selected first-wave step rows to `RUNNING`.

**Endpoint:** `PUT http://cases:8083/cases/<case_id>/steps/<step_uuid>`

```python
# From case response, get only the first-wave step UUIDs that are actually being dispatched.
# Do not mark the entire process RUNNING up front if downstream steps still depend on outputs.
steps = response['steps']
step_uuids = [step['id'] for step in steps if step['step_id'] in {"step_1"}]  # example

for step_uuid in step_uuids:
    update = {"status": "RUNNING"}
    req = PUT /cases/<case_id>/steps/<step_uuid>
    body: {"status": "RUNNING"}
```

**Common Errors:**

### Error: "step not found" (HTTP 404)

**Cause:** Using sequential step numbers (step_1, step_2) instead of actual UUIDs

**Solution:** Always fetch the case first to get actual step UUIDs from `response['steps'][N]['id']`. Step IDs are unique UUIDs, not sequential identifiers.

### Error: "No steps found" (HTTP 404)

**Cause:** Trying to access `response['case']['steps']` instead of `response['steps']`

**Solution:** The cases API returns steps at the top level `response['steps']`, NOT nested under `response['case']['steps']`. Make sure to use the correct path.
```
### Error: "process doc must declare an exhaustive ## Variables table"

**Cause:** `process_source` missing or incomplete, or Variables section omitted

**Solution:** Use the FULL process markdown from `/hub/base/ops/processes/process-queued-review.md` including the entire `## Variables` table

### Error: HTTP 422 when posting multiple slots

**Cause:** API expects single slot per request, not array

**Solution:** Make 8 separate POST requests, one for each slot

### Error: HTTP 422 when updating steps with result_json

**Cause:** `result_json` validation issue

**Solution:** Update steps with `{"status": "RUNNING"}` only. Workers should write declared outputs through `POST /cases/<case_id>/slots` or the case toolset with an `agent_run_id`. The cases service will derive `COMPLETED`.

### ⚠️ Automatic Dispatch After Slot Population

**Important Discovery:** When slots are populated for a case, the worker dispatch may happen **automatically** rather than requiring a manual dispatch call. If `POST /cases/<case_id>/dispatch` returns 404, this is likely normal — the case service may be triggering the first ready step automatically after slot population.

To verify dispatch occurred, check the case status via `GET /cases/<case_id>` and look for steps with status `RUNNING`.

### Error: HTTP 404 on dispatch endpoint

**Cause:** The dispatch endpoint may not exist or may be handled automatically

**Solution:** After populating slots, check case status via `GET /cases/<case_id>`. If steps show `RUNNING`, dispatch was successful and no further action is needed.

### Error: TypeError slicing integer slot values

**Cause:** Attempting to slice a non-string slot value (e.g., `duration_ms` as integer)

**Solution:** Handle slot values as generic data types:

```python
for slot in slots:
    value_str = slot['value']
    if isinstance(value_str, str):
        value_preview = value_str[:50] + "..." if len(value_str) > 50 else value_str
    else:
        value_preview = str(value_str)
    print(f"  Setting slot: {slot['name']} = {value_preview}...")
```

---

## Workflow Steps (Automatic Execution)

Once case is created and slots populated, the worker executes:

| Step | Action | Executor Skill |
|------|--------|----------------|
| 1 | Load review record and materialize assets | - |
| 2 | Transcribe audio | `transcribe-review-audio` |
| 3 | Resolve component names from concrete event targets | - |
| 4 | Annotate transcript / segments | `annotate-review-transcript` |
| 5 | Build canonical `review_packet.json` with per-event `target_events`, actionability, negative evidence, and implementation handoff | `extract-review-observations` |
| 6 | Bind codebase context per feedback item or emit explicit deferred/blocked binding records | - |
| 7 | Render review document from packet | - |
| 8 | Update review status with packet status | - |
| 9 | Log in daily note | - |

---

## Common Errors & Solutions

### Error: "process doc must declare an exhaustive ## Variables table"

**Cause:** `process_source` missing or incomplete, or Variables section omitted

**Solution:** Use the FULL process markdown from `/hub/base/ops/processes/process-queued-review.md` including the entire `## Variables` table

### Error: HTTP 422 when posting multiple slots

**Cause:** API expects single slot per request, not array

**Solution:** Make 8 separate POST requests, one for each slot

### Error: HTTP 404 - "step not found"

**Cause:** Using sequential step numbers (step_1, step_2) instead of actual UUIDs

**Solution:** Always fetch the case first to get actual step UUIDs from `response['case']['steps'][N]['id']`. Step IDs are unique UUIDs, not sequential identifiers.

### Error: HTTP 422 when posting multiple slots

**Cause:** API expects single slot per request, not array

**Solution:** Make 8 separate POST requests, one for each slot

### Error: HTTP 422 when updating steps with result_json

**Cause:** `result_json` validation issue

**Solution:** Update steps with `{"status": "RUNNING"}` only, set result_json through workflow execution

---

## Best Practices

1. **Use Python/requests library** — Shell escaping with markdown containing backticks and quotes is unreliable. Python scripts are more robust.

2. **Always fetch case details** — Step IDs are UUIDs that must be retrieved from the case response, never guessed or assumed.

3. **Include full process markdown** — The `## Variables` table is mandatory. Without it, the process won't validate.

4. **Populate slots one at a time** — The API does not accept batch slot updates.

5. **Update only the dispatched first wave to RUNNING** — The worker will write outputs with provenance and the cases service will advance completion/readiness.

---

## Success Criteria

- ✅ Case status progresses through `OPEN` / `READY` / `RUNNING` / `COMPLETED|FAILED`
- ✅ Root review slots are populated with correct values
- ✅ Native pipeline produces `review_packet.json` with per-event target alignment
- ✅ Packet includes actionability, negative evidence, and `implementation_handoff.implementation_tasks`
- ✅ Missing source binding is visible as deferred/blocked per feedback item, never silent success
- ✅ Step completion is driven by declared outputs written with `agent_run_id`
- ✅ Workflow triggers automatic downstream readiness as outputs arrive
- ✅ Final review document is rendered from the packet at `~/claude-hub/notes/review {review_id_short}.md`
