---
title: "Annotate transcript — Pass 4: resolve and clean"
doc_type: skills
tags: [review, transcript, annotation, coherence]
---

# Annotate transcript — Pass 4: resolve and clean

## Purpose

Produce the final, handoff-ready version of the annotated transcript. Collapse all
inline bracket annotations to clean parenthetical references. Flag anything still
unresolved with an explicit `[?unresolved]` marker. This is the version downstream
processes read when they need element attribution from the transcript.

Runs after: Pass 3 (shape back-reference).

---

## Inputs

- Pass 3 annotated transcript (from transcript note `## Pass 3` section)

---

## Instructions

### 1. Collapse annotations

Replace every `[→ ComponentName]` and `[→ gesture shape — ComponentName]` with an
inline parenthetical: `(ComponentName)`.

Where both a gesture and a pointer-move annotated the same word, use the gesture —
it is the more intentional signal.

### 2. Flag unresolved items

Any deictic word still unresolved after Passes 2 and 3: mark `[?unresolved]` with
a one-line reason in a trailing note block.

Do not drop unresolved items — visibility of ambiguity is the goal.

### 3. Preserve transcription error flags

Keep all `[?transcription — ...]` markers from Pass 2 unchanged.

### 4. Output

Clean prose version of the transcript with:
- Deictic words resolved to `(ComponentName)` inline
- `[?unresolved]` for anything still ambiguous
- `[?transcription — ...]` for confirmed transcription errors
- Unresolved count in a trailing summary line

Write to the `## Pass 4 — Resolved` section of the transcript note,
replacing the pending stub.

---

## Quality gates

- [ ] All `[→ ...]` brackets collapsed to `(ComponentName)` form
- [ ] Gesture attribution preferred over pointer-move where both exist
- [ ] `[?unresolved]` applied to every word that couldn't be resolved
- [ ] Transcription error flags preserved from Pass 2
- [ ] Unresolved count reported in summary line
