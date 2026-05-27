---
title: "Process queued review"
doc_type: processes
tags: [review, feedback, audio, transcription, annotation]
dispatch_profile: frank
---

# Process queued review

## When to use

Use when a `review_submitted` event arrives — a user has completed a screen recording
review session in ZenithOS and the audio + interaction events are ready for processing.
Trigger: `event_type = review_submitted`, payload contains `audio_asset_id`,
`events_asset_id`, and `review_id`.

## What this process does

Turns a raw review submission (audio recording + interaction events) into a
**processed review packet and review document** — a faithful, reviewer-voice capture
of what was said and shown, with typed feedback points and resolved element targets.
The canonical machine-readable artifact is `review_packet.json`; the markdown review
is rendered from that packet for human handoff. Creating implementation issues is not
this pipeline's job.

---

## What Frank provides

Frank invokes this process when a `review_submitted` event fires. The worker receives:

**1. This process document** — the full pipeline instructions.

**2. Context payload** — injected by Frank at invocation time:

```json
{
  "review_id": "bd1844d0-5555-4ad9-9441-cc4712a47a44",
  "subject_id": "http://localhost:3000/?reviewMode=on",
  "submitted_by": "Gabriel",
  "reviewed_at": "2026-04-17T15:26:35Z",
  "duration_ms": 53381,
  "audio_asset_id": "380d4cdd-...",
  "events_asset_id": "cf13bc0f-..."
}
```

---

## Principle

**The verbal cue is the feedback. The stroke is the pointer.**

Issues come from what the reviewer said, not from where they drew. A stroke without
speech is a spatial hint, not a feedback point. A verbal statement without a stroke is
still valid feedback — its target is the whole screen or general context.

**Feedback points are in the reviewer's voice.** The output captures what the reviewer
said and showed — "The X button moves to the wrong position", "This needs more contrast"
— not a redesigned spec. If the verbal cue is too fragmentary to extract a coherent
observation, no feedback point is written for it.

---

## Required capabilities

### Skills
- `transcribe-review-audio`
- `annotate-review-transcript`
- `extract-review-observations`

### Tools
- `local_whisper`
- `update_review_status`

### Environment
- `STT_PROVIDER`
- `STT_MODEL`
- `STT_FALLBACK_PROVIDER`
- `STT_AUDIO_PREPROCESSOR` (default `none`; optional `elevenlabs_audio_isolation` for noisy audio)
- `ELEVENLABS_API_KEY` (required when `STT_PROVIDER=elevenlabs` or `STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation`)
- `STT_HTTP_URL` (required for local fallback)

### Toolsets
- `file`
- `browser`

### Resources
- `review record`
- `review audio asset`
- `review events asset`
- `review assets workspace`
- `vault notes workspace`
- `vault daily note`
- `subject codebase`
- `review status record`

---

## Steps

### Step 1 — Load review record

**Executor:** `frank`
**Assignee:** `frank`

**Input:** Frank context payload — `review_id`, `audio_asset_id`, `events_asset_id`,
`subject_id`, `submitted_by`, `reviewed_at`, `duration_ms`

**Required Resources:** `review record`
`review audio asset`
`review events asset`
`review assets workspace`

**Processing:** Confirm both assets exist through the review asset API, materialize them into the case runtime asset workspace, and read the events JSON into memory.

**Output (process state):**
```json
{
  "review_id_short": "bd1844d0",
  "audio_asset_path": "case runtime asset path for {audio_asset_id}",
  "events": [ ...full events array... ]
}
```

---

### Step 2 — Transcribe audio

**Executor:** `frank`
**Assignee:** `worker`

**Skills:** `transcribe-review-audio`

**Tools:** `local_whisper`

**Input:** `audio_asset_path`, `events`

**Required Resources:** `review audio asset`
`review assets workspace`

**Processing:** Transcribe `audio_asset_path` into a verbatim transcript through Frank's STT provider boundary. Production uses ElevenLabs Scribe v2 batch (`STT_PROVIDER=elevenlabs`, `STT_MODEL=scribe_v2`) with local Whisper/STT HTTP available as fallback. Use the interaction timeline in `events` only to align the transcript with the review session and preserve the correct `audio_offset_ms` for downstream annotation.

**Output (process state):**
```json
{
  "transcript": "verbatim full spoken text",
  "audio_offset_ms": 5200,
  "words": [
    { "text": "this", "start_ms": 5340, "end_ms": 5520 }
  ]
}
```

---

### Step 3 — Resolve component names

**Executor:** `frank`
**Assignee:** `worker`

**Input:** `subject_id`, `events`

**Required Resources:** `subject codebase`
`review assets workspace`

**Suggested Toolsets:** `browser`

**Processing:** Resolve the actual component names for the reviewed surface before any transcript annotation, observation extraction, or review-document writing. Use `subject_id` to identify the subject app/codebase, inspect the component tree or source code, and map review event targets/spatial regions to component names. Prefer the concrete `target` field from individual interaction events over inferred aggregate bounds. Capture selectors and source file/line evidence when available so later steps can quote real component targets instead of raw spatial guesses. If the URL is unreachable or the codebase cannot be identified, preserve unresolved spatial descriptors with explicit `resolution_status: "unresolved"` entries rather than inventing component names.

**Output (process state):**
```json
{
  "component_names": [
    {
      "component": "LandingPage notification card dismiss button",
      "source": "src/components/hub/LandingPage.tsx:166-173",
      "selectors": [".zh-notification-card__dismiss"],
      "aliases": ["exit icon", "x icon", "dismiss icon", "this guy"],
      "spatial_hint": "small x button at the corner of each notification card",
      "resolution_status": "resolved"
    }
  ]
}
```

---

### Step 4 — Annotate transcript

**Executor:** `frank`
**Assignee:** `worker`

**Skills:** `annotate-review-transcript`

**Input:** `transcript`, `words`, `audio_offset_ms`, `events`, `component_names`, `review_id`, `submitted_by`,
`reviewed_at`, `subject_id`, `audio_asset_id`

**Required Resources:** `vault notes workspace`
`review assets workspace`
`subject codebase`

**Processing:** Run the annotation passes that resolve timing, gesture linkage, review-context markup, and component targets. The transcript annotation must use `component_names`; do not annotate gestures as final targets using only raw spatial bounds unless component resolution explicitly failed for that region. Write the transcript note into the vault so downstream steps can reference both the annotated text and the saved note path.

**Output (process state):**
```json
{
  "transcript_note_path": "vault notes workspace transcript path for {review_id_short}",
  "resolved_transcript": "...(Pass 4 annotated text with component names)..."
}
```

---

### Step 5 — Extract observations

**Executor:** `frank`
**Assignee:** `worker`

**Skills:** `extract-review-observations`

**Input:** `resolved_transcript`, `words`, `events`, `component_names`

**Required Resources:** `review assets workspace`
`subject codebase`

**Processing:** Convert the component-annotated transcript and aligned event stream into reviewer-voice observations, separating matched observations from silent annotations and filtered points. Every observation target must be tied to an actual component name when resolution is available; unresolved targets must carry the explicit unresolved spatial descriptor produced during component resolution. Preserve direct quotes in `what_they_said`/`talking_point`, include selectors/source evidence when available, and keep this step as structured process state only; it does not write notes or specs.

Local-run finding: build the canonical `review_packet.json` here. Normalize raw events into `target_events`, preserve the original `events_asset_path`, align transcript segments against individual target events, and never use a single aggregate target span for every repeated target occurrence. Repeated targets must only attach to transcript segments whose time window overlaps that specific target event.

The packet must include `actionability`, `negative_evidence`, and `implementation_handoff` sections. Silent gestures and filtered/fragment points are preserved as negative evidence and must not become implementation tasks without matching speech.

**Output (process state):**
```json
{
  "observations": [
    {
      "observation_id": "obs-1",
      "type": "request",
      "component": "LandingPage notification card dismiss button",
      "selectors": [".zh-notification-card__dismiss"],
      "talking_point": { "text": "I just want to move this exit icon", "start_ms": 1960, "end_ms": 13460 },
      "what_they_said": "I just want to move this exit icon... I want to move them so that they are over the corner",
      "issue": "The dismiss x buttons on notification cards should sit over the card corner instead of floating inset from the card edge.",
      "evidence": "The dismiss button is absolutely positioned inside the notification card.",
      "gestures": [{ "gesture_id": "g-2", "shape": "circle", "bounds": { "x": 309, "y": 43, "width": 23, "height": 23 }, "component": "LandingPage notification card dismiss button" }],
      "match_type": "verbal_and_gesture"
    }
  ],
  "target_events": [
    { "target": "LandingPage notification card dismiss button", "start_ms": 1960, "end_ms": 2360, "event_index": 42 }
  ],
  "review_packet_path": "case runtime artifact path for review_packet.json",
  "review_packet_status": "review_packet_ready",
  "actionability": {
    "actionable_now": [],
    "needs_human_clarification": [],
    "design_preference": [],
    "non_issue": [],
    "discarded_or_filtered": []
  },
  "negative_evidence": {
    "silent_annotations": [],
    "filtered_points": [],
    "discarded_events": []
  },
  "implementation_handoff": {
    "implementation_tasks": [],
    "open_questions": [],
    "non_goals": [],
    "files_to_inspect_first": [],
    "verification_notes": []
  },
  "silent_annotations": [],
  "filtered_points": []
}
```

---

### Step 6 — Bind feedback to codebase context

**Executor:** `frank`
**Assignee:** `worker`

**Input:** `observations`, `silent_annotations`, `filtered_points`, `resolved_transcript`, `component_names`, `events`, `subject_id`

**Required Resources:** `subject codebase`
`review assets workspace`

**Suggested Toolsets:** `file`
`browser`

**Processing:** Bring the extracted feedback into contact with the actual implementation before writing the final review document. Inspect the code/components/selectors referenced by `component_names`, read nearby implementation and styling, and bind each observation to concrete codebase context when the subject codebase is reachable. This is an analysis/context step, not a fix-planning or implementation step: provide likely causes, relevant files/selectors/state logic, confidence, and caveats without inventing requirements or acceptance criteria. If source binding cannot be completed in the local run, emit one `deferred` or `blocked` binding per feedback item with `feedback_item_id`, reason, selectors/target refs, caveats, and open questions. Empty source binding is invalid unless the packet is explicitly degraded and carries a reason.

**Output (process state):**
```json
{
  "codebase_context": [
    {
      "observation_id": "obs-1",
      "component": "LandingPage notification card dismiss button",
      "selectors": [".zh-notification-card__dismiss"],
      "references": [
        { "path": "src/components/hub/LandingPage.tsx", "lines": "166-173", "reason": "dismiss button markup" },
        { "path": "src/components/hub/LandingPage.css", "lines": "217-239", "reason": "absolute positioning and hover visibility" }
      ],
      "likely_cause": "Dismiss control is positioned inset inside the card instead of anchored across the card corner.",
      "reasoning": "The reviewer pointed at the x controls and asked for them over the corner; the CSS currently uses top/left offsets inside the card box.",
      "confidence": "medium",
      "caveats": []
    }
  ]
}
```

---

### Step 7 — Write review document

**Executor:** `frank`
**Assignee:** `worker`

**Input:** `observations`, `silent_annotations`, `resolved_transcript`, `component_names`, `codebase_context`, `review_packet_path`, `review_packet_status`, `review_id`,
`subject_id`, `submitted_by`, `reviewed_at`, `duration_ms`, `transcript_note_path`

**Required Resources:** `vault notes workspace`

**Processing:** Write the processed review note into the vault as a faithful,
reviewer-voice handoff artifact for the downstream planning LLM. This step must run only after component resolution, transcript annotation, observation extraction, review-packet creation, and codebase-context binding/deferment have connected the reviewer's comments to actual component names and likely implementation context. Render the markdown review from `review_packet.json` rather than reconstructing feedback from scratch. Include packet status, implementation handoff tasks, source-binding status, open questions, non-goals, and negative evidence warnings. Do not create ISS notes or acceptance criteria here.

**Output (process state):**
```json
{
  "review_note_path": "vault notes workspace review path for {review_id_short}"
}
```

**Review note format:**

```markdown
---
description: "Review of {subject_domain} by {submitted_by} — {N} issues identified"
type: note
domain: [projects]
arena: [Zenith]
created: {reviewed_at date}
author: "[[Claude Code]]"
areas:
  - [[reviews]]
---

# Review {review_id_short} — {subject_url}

**Submitted by:** {submitted_by} · **Duration:** {Xm Ys} · **Reviewed:** {date} · **Transcript:** [[transcript {review_id_short}]]

---

## What the reviewer said

{Narrative paragraph(s) in the reviewer's voice — what they noticed and commented
on, in order. Quote verbal cues directly. Reference component names where resolved.}

---

## Component resolution used before annotation

{Bullets from `component_names`: component label, selectors, source path/line evidence, aliases or unresolved spatial descriptor. This section exists because component names are upstream process state, not a cleanup pass after writing.}

---

## Issues identified

### {N}. [{type}] {one-line description}

**What they said:** "{talking_point.text}"
**Where:** {Resolved component name plus selector/source evidence from `component_names`, or explicit unresolved spatial description}
**The issue:** {one or two sentences from the reviewer's perspective}
**Evidence in code/events:** {source line, selector, event target, or gesture evidence when available}
**Implementation context:** {Relevant `codebase_context` summary: likely cause, references, confidence, caveats. Keep this as situational awareness, not fix instructions.}

---

## Unresolved annotations

{Silent gestures with no talking point match. One bullet per gesture.}
```

**Rules:**
- Written from reviewer's perspective — no "The reviewer observed that..."
- No acceptance criteria. No spec language. No ISS references.
- Talking points quoted directly.
- `approval`, `pointing`, and `fragment` points do not appear as issues.

---

### Step 8 — Update review status

**Executor:** `frank`
**Assignee:** `worker`

**Input:** `review_id`, `review_note_path`, `review_packet_path`, `review_packet_status`

**Required Resources:** `review status record`

**Tools:** `update_review_status`

**Processing:** Mark the review record for `review_id` as `processed`, preserving existing review metadata and recording the generated `review_note_path`, `review_packet_path`, and `review_packet_status`.

This is the only review-status writeback in the process. Emit `review_status_updated` only after the gateway/status record confirms the write succeeded.

**Output (process state):**
```json
{
  "review_status_updated": {
    "review_id": "bd1844d0-5555-4ad9-9441-cc4712a47a44",
    "status": "processed",
    "review_note_path": "vault notes workspace review path for {review_id_short}"
  }
}
```

---

### Step 9 — Log in daily note

**Executor:** `frank`
**Assignee:** `worker`

**Input:** `review_id_short`, `subject_id`, `transcript_note_path`, `review_note_path`, `observations`, `review_id`, `review_status_updated`

**Required Resources:** `vault daily note`
`review status record`

**Processing:** Append the following entry to the operator vault daily note (`notes/YYYY-MM-DD.md`) only after the review status update has succeeded:

```
- HH:MM [Zenith] [create] [[Claude Code]] processed review {review_id_short}, updated review status, and created [[review {review_id_short}]] and [[transcript {review_id_short}]] — {N} issues identified from review of {subject_domain}
```

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `review_id` | string (UUID) | Unique identifier for the review submission |
| `audio_asset_id` | string (UUID) | Asset ID of the `.webm` audio recording |
| `events_asset_id` | string (UUID) | Asset ID of the interaction events JSON file |
| `subject_id` | string (URL) | URL of the subject being reviewed |
| `submitted_by` | string | Name of the reviewer who submitted |
| `reviewed_at` | string (ISO 8601) | Timestamp when the review was submitted |
| `duration_ms` | number | Duration of the audio recording in milliseconds |
| `audio_asset_path` | string (path) | Filesystem path to the audio recording asset |
| `events` | array | Interaction events from the review session (strokes, clicks, timeline) |
| `transcript` | string | Verbatim spoken text extracted from audio transcription |
| `audio_offset_ms` | number | Milliseconds of silence before speech begins in the recording |
| `words` | array | Word-level timestamps `{ text, start_ms, end_ms }` from transcription |
| `component_names` | array | Resolved component names, aliases, selectors, spatial hints, and resolution status for the reviewed surface |
| `target_events` | array | Normalized per-event target timeline derived from interaction events; used for segment alignment without aggregate-span false positives |
| `review_packet_path` | string (path) | Case artifact path to canonical `review_packet.json` |
| `review_packet_status` | string | Packet quality/status, e.g. `review_packet_ready`, `needs_source_binding`, `needs_human_review`, `transcript_only`, or `failed` |
| `actionability` | object | Feedback triage buckets: actionable now, needs clarification, design preference, non-issue, discarded/filtered |
| `negative_evidence` | object | Silent annotations, filtered points, and discarded events preserved with reasons so agents know what not to implement |
| `implementation_handoff` | object | Delegation payload with implementation tasks, open questions, non-goals, files to inspect first, and verification notes |
| `transcript_note_path` | string (path) | Vault path to the generated transcript note |
| `resolved_transcript` | string | Annotated transcript after timing, gesture, context, and component-name passes |
| `observations` | array | Extracted feedback observations with type, talking point, and gestures |
| `silent_annotations` | array | Gesture annotations with no matching talking point |
| `filtered_points` | array | Talking points filtered out during observation extraction |
| `codebase_context` | array | Situational implementation context binding observations to relevant components, selectors, files, likely causes, confidence, and caveats |
| `review_id_short` | string | First 8 characters of `review_id`, used as the short identifier in vault note filenames |
| `review_note_path` | string (path) | Vault path to the generated review document note |
| `review_status_updated` | object | Confirmation payload emitted only after the review status record is successfully marked processed |

---

## Artifacts and side effects

| Artifact | Location | Description |
|---|---|---|
| Review packet | case runtime artifacts: `review_packet.json` | Canonical machine-readable packet with normalized events, target timeline, segments, feedback items, packet quality/status, and artifact pointers |
| Processed review doc | operator vault note: `review {review_id_short}.md` | Reviewer-voice document rendered from `review_packet.json`; issues identified, component names resolved |
| Transcript note | operator vault note: `transcript {review_id_short}.md` | Verbatim transcript with all annotation passes |
| Review status update | review status record | `status: processed`, preserving metadata and recording `review_note_path` |

All other intermediate data remains process state. The review packet is the durable
case artifact for machine consumers; the vault review note is the human-facing handoff.
