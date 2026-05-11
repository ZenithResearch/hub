---
title: "Annotate transcript — Pass 2: temporal deictic"
doc_type: skills
tags: [review, transcript, annotation, deictic]
---

# Annotate transcript — Pass 2: temporal deictic

## Purpose

Walk the verbatim transcript and resolve deictic words ("this", "here", "them", etc.)
to the element or gesture active at that exact moment in the event timeline. Output is
written as an annotated version of the transcript with inline bracketed references.
This pass uses only what is available at the moment of each word: pointer-move, click
events, and strokes in progress. Completed gestures are not yet available — those are
resolved in Pass 3.

---

## Inputs

From process state:
- `transcript` — verbatim full text
- `words` — list of `{ text, start_ms, end_ms }` in event timeline ms
- `events` — full event array

---

## Deictic targets

Words that trigger resolution attempts:

`this`, `these`, `it`, `it's`, `them`, `they`, `they're`, `that`, `here`, `there`

Plus any word that clearly points to a UI element in context.

**Skip anaphoric references** — if the referent is named in the same clause
("the review ones will have that on them"), do not annotate. Annotate only
spatial/deictic references.

---

## Resolution tiers — mode-aware, stop at first hit

Determine capture mode at word timestamp from `capture-mode-changed` events.

**Drawing mode** (tier order):
1. **Stroke in progress** — any stroke whose `[start_ms, end_ms]` contains the word
   timestamp → `word [→ gesture_id in progress (shape) — ComponentName]`
2. **Active highlight** — highlight event active at this timestamp; include highlighted
   text AND element → `word [→ "highlighted text" in ComponentName]`
3. **Non-canvas click** within ±2,000ms → `word [→ ComponentName]`
4. **Nearest pointer-move** — use `pointer-move.target` if not `"canvas"`
   → `word [→ ComponentName @(x,y)]`

**Highlight mode** (tier order): highlight → stroke → click → pointer-move

**Unannotated** — no hit at any tier; leave the word unchanged. Log for Pass 3.

---

## Transcription errors

Flag words that are syntactically or semantically implausible:
`word [?transcription — note]`

Do not correct — annotate only.

---

## Output

1. Annotated transcript string — verbatim text with inline `[→ ...]` references
2. Summary line listing: tiers used (stroke/click/pointer-move counts),
   anaphoric skips, and any words left unannotated for Pass 3

Write both to the `## Pass 2 — Temporal annotation` section of the transcript note
(`~/claude-hub/notes/transcript {review_id_short}.md`), replacing the pending stub.

---

## Quality gates

- [ ] Only deictic words are annotated — non-pointing words left unchanged
- [ ] Anaphoric references are skipped (referent named in same clause)
- [ ] Mode-aware tier order applied correctly
- [ ] Transcription error flags use `[?transcription — ...]` form
- [ ] Unannotated deictics logged explicitly in the summary line
