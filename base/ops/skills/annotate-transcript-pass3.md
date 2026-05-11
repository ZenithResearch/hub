---
title: "Annotate transcript — Pass 3: shape back-reference"
doc_type: skills
tags: [review, transcript, annotation, gestures, shapes]
---

# Annotate transcript — Pass 3: shape back-reference

## Purpose

Upgrade the Pass 2 annotations using completed gesture shapes and resolved component
names (available after Step 7b). Also catches deictic words that Pass 2 left
unannotated by expanding the gesture match window to ±3,000ms.

Runs after: Step 4 (gesture reconstruction) and Step 7b (component name resolution).

---

## Inputs

From process state:
- Pass 2 annotated transcript (from transcript note `## Pass 2` section)
- `gestures` — fully reconstructed, with `shape` and resolved component names
- `words` — with event timeline timestamps

---

## Instructions

### 1. Upgrade gesture stubs

Walk through the Pass 2 output. For any annotation that referenced a gesture with raw
bounds only (`gesture_id in progress`), replace it with the resolved component name
now available from Step 7b:

Before: `[→ g-1 in progress (circle) — bounds only]`
After: `[→ g-1 circle — ComponentName]`

### 2. Resolve unannotated deictics

For each word left unannotated by Pass 2: re-check against all completed gestures
using an expanded ±3,000ms window. If a gesture falls within range, annotate with its
resolved component name and mark `(Pass 3)`.

Do not re-annotate words already annotated in Pass 2.

### 3. Output

Updated annotated transcript with:
- All Pass 2 gesture stubs upgraded to component names
- Previously unannotated deictics resolved where possible, marked `(Pass 3)`
- A summary line noting: upgrades made, new resolutions in Pass 3

Write to the `## Pass 3 — Shape back-reference` section of the transcript note,
replacing the pending stub.

---

## Quality gates

- [ ] Only Pass 2 stubs upgraded — no re-annotation of already-resolved words
- [ ] Pass 3 resolutions marked `(Pass 3)` inline
- [ ] Still-unresolved words carried forward (not silently dropped)
- [ ] Summary line lists upgrade count and new Pass 3 resolutions
