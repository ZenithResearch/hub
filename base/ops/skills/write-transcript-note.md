---
title: "Write transcript note"
doc_type: skills
tags: [review, transcript, vault, note]
---

# Write transcript note

## Purpose

Persist the verbatim transcript to the vault as a durable note. Creates the note
file and stubs out sections for the three annotation passes. If the file already
exists, skip — do not overwrite.

---

## Instructions

### 1. Compute review_id_short

Take the first 8 characters of the `review_id` UUID.

### 2. Check for existing note

If `~/claude-hub/notes/transcript {review_id_short}.md` already exists, stop and
return `{ "skipped": true, "reason": "already exists" }`.

### 3. Write the note

Path: `~/claude-hub/notes/transcript {review_id_short}.md`

```markdown
---
description: "Verbatim timestamped transcript of review {review_id_short} audio — {submitted_by} reviewing {subject_domain}"
type: note
domain: [projects]
arena: [Zenith]
created: {reviewed_at date}
author: "[[Claude Code]]"
areas:
  - [[reviews]]
---

# Transcript — review {review_id_short}

**Source:** audio asset `{audio_asset_id}`
**Transcribed by:** ElevenLabs Scribe v1
**Audio offset:** {audio_offset_ms}ms · **Duration:** {duration}s · **Words:** {word_count}

---

## Verbatim

{transcript — full string, no edits}

---

## Pass 2 — Temporal annotation

*(pending — see annotate-transcript-pass2 skill)*

---

## Pass 3 — Shape back-reference

*(pending — see annotate-transcript-pass3 skill)*

---

## Pass 4 — Resolved

*(pending — see annotate-transcript-pass4 skill)*

---

## Timestamped segments

| ms (start → end) | text |
|---|---|
{one row per segment: start_ms → end_ms | verbatim text}

---

## Provenance

- **Review record:** `data/reviews/{review_id}.json`
- **Audio asset:** `data/reviews/assets/{audio_asset_id}` (`{mime_type}`, {size})
- **Review note:** [[review {review_id_short}]]
```

### 4. Output

```json
{ "transcript_note_path": "~/claude-hub/notes/transcript {review_id_short}.md" }
```

---

## Quality gates

- [ ] Note written only if it did not already exist
- [ ] `## Verbatim` contains full transcript string, no edits
- [ ] `## Timestamped segments` table populated (one row per segment)
- [ ] Pass sections contain the pending stub — not left blank
