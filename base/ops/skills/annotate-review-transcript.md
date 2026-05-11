---
title: "Annotate review transcript"
doc_type: skills
tags: [review, transcript, annotation]
---

# Annotate review transcript

## Purpose

Run all four annotation passes on the verbatim transcript and write the result as a
durable note to the vault. Each pass builds on the previous one. Internally calls
the four annotation sub-skills in sequence.

---

## Inputs

From process state:
- `transcript`, `words` (with event-timeline `start_ms`/`end_ms`), `audio_offset_ms`
- `events` — full event array (for deictic resolution and gesture shapes)
- Review context: `review_id`, `submitted_by`, `reviewed_at`, `subject_id`,
  `audio_asset_id`

---

## Execution

### 1. Write transcript note   [sub-skill: write-transcript-note]

Create `~/claude-hub/notes/transcript {review_id_short}.md` with:
- `## Verbatim` — full transcript, no edits
- `## Pass 2`, `## Pass 3`, `## Pass 4` — pending stubs (populated below)
- `## Timestamped segments` — one row per segment
- `## Provenance`

Skip if the file already exists.

### 2. Pass 2 — Temporal deictic annotation   [sub-skill: annotate-transcript-pass2]

Walk the verbatim transcript. For each deictic word, resolve to the element or gesture
active at that word's event-timeline timestamp. Mode-aware tier: stroke-in-progress →
active highlight → click (±2s) → pointer-move. Unannotated deictics logged for Pass 3.

Write result to `## Pass 2` section of transcript note.

### 3. Pass 3 — Shape back-reference   [sub-skill: annotate-transcript-pass3]

Upgrade Pass 2 gesture stubs using completed gesture shapes (available from events JSON).
Re-check unannotated deictics with ±3,000ms window. CSS selectors from click events are
the source of component names at this stage — no codebase lookup required.

Write result to `## Pass 3` section of transcript note.

### 4. Pass 4 — Resolve and clean   [sub-skill: annotate-transcript-pass4]

Collapse all `[→ ...]` brackets to `(ComponentName)`. Flag unresolved as `[?unresolved]`.
Preserve `[?transcription — ...]` flags. Produce the final handoff-ready version.

Write result to `## Pass 4` section of transcript note.

---

## Output

Transcript note fully populated with all 4 passes.

```json
{
  "transcript_note_path": "~/claude-hub/notes/transcript {review_id_short}.md",
  "resolved_transcript": "...(Pass 4 text)...",
  "unresolved_deictics": [ "word at Nms — reason" ]
}
```

---

## Quality gates

- [ ] Transcript note written (or already existed — skip logged)
- [ ] All 4 passes populated in the note — no pending stubs remaining
- [ ] Pass 4 `(ComponentName)` references derived from CSS selectors in events, not guessed
- [ ] `[?unresolved]` applied to every deictic that couldn't be resolved
