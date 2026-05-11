---
title: "Reconstruct review gestures"
doc_type: skills
tags: [review, events, gestures, shapes, annotation]
---

# Reconstruct review gestures

## Purpose

Parse the events JSON and produce a list of **meaningful gestures** — completed
annotations with recognizable shapes and clear element targets. This skill
operates on the events payload only, with no transcript. The output is spatial:
what the reviewer drew, where they drew it, and what element it points at.

A "gesture" is not a stroke. A stroke is a raw input event. A gesture is the
meaningful unit: one or more related strokes that together form a complete
annotation (a circle, a bracket, an underline). Degenerate marks, zero-size
strokes, and accidental touches are filtered out.

---

## Instructions

### 1. Load the events JSON

Parse all events ordered by `elapsedMs`. Collect `stroke-started` /
`stroke-point` / `stroke-ended` sequences, grouping points by `strokeId`.

---

### 2. Filter degenerate strokes

Before classification, discard strokes where:

- `bounds.width == 0 AND bounds.height == 0` (zero-size mark — accidental touch)
- Total path length < 10px (single tap, not a gesture)
- Duration < 80ms (too fast to be intentional)

Filtered strokes go to `discarded_marks` — logged but not processed further.

---

### 3. Classify each stroke by shape

Compute from the point path:

| Metric | Formula |
|--------|---------|
| Closure ratio | `dist(first_point, last_point) / total_path_length` |
| Straightness | `dist(first_point, last_point) / total_path_length` |
| Aspect ratio | `bounds.width / bounds.height` |

**Shape classification:**

| Closure | Straightness | Aspect | Result |
|---------|-------------|--------|--------|
| < 0.25  | any         | 0.5–2  | circle |
| < 0.25  | any         | > 2    | bracket |
| > 0.75  | > 0.75      | > 3    | underline |
| > 0.75  | > 0.75      | any, arrowhead at end | arrow |
| > 0.75  | > 0.75      | any    | line |
| any     | < 0.4       | any, high reversals | scribble |
| other   |             |        | freeform |

Arrow detection: check if the final 15% of path points reverse direction
> 45° from the main path. If so, classify as arrow; the tip is the last point
before the reversal.

---

### 4. Group strokes into gestures

Consecutive strokes drawn within 1000ms of each other and spatially overlapping
(bounds overlap > 20%) are candidates for grouping into a single gesture.
A group becomes one gesture if the combined shape makes sense as a unit:

- Two parallel vertical lines flanking content → bracket gesture
- A circle followed immediately by a re-circle of the same area → emphasis circle
- Sequential underlines on the same text region → multi-line underline

If strokes are not within 1000ms of each other or do not spatially overlap,
they are separate gestures.

**Gesture output:**
```json
{
  "gesture_id": "g-1",
  "shape": "circle",
  "bounds": { "x": 24, "y": 82, "width": 24, "height": 29 },
  "strokes": ["stroke-4"],
  "elapsed_start_ms": 41000,
  "elapsed_end_ms": 41800,
  "css_selector": null,
  "screenshot_ref_id": "screenshot-4"
}
```

---

### 5. Extract CSS selectors

For each gesture, find `click` events within ±1000ms of the gesture window
where `event.target ≠ "canvas"`. The nearest click's `target` is the
`css_selector`. If none found, `css_selector: null`.

---

### 6. Verify gesture completeness

A gesture is **meaningful** if it has a recognizable shape (circle, bracket,
underline, arrow, line) and the element it refers to can be described from
either the `css_selector` or the `bounds`.

A `freeform` or `scribble` gesture is only meaningful if it has a clear
spatial focus (tight bounds) and the bounds correspond to a known element.
Otherwise it goes to `ambiguous_gestures`.

---

### 7. Store in process state

```json
{
  "gestures": [
    {
      "gesture_id": "g-1",
      "shape": "circle",
      "bounds": { "x": 24, "y": 82, "width": 24, "height": 29 },
      "strokes": ["stroke-4"],
      "elapsed_start_ms": 41000,
      "elapsed_end_ms": 41800,
      "css_selector": null,
      "screenshot_ref_id": "screenshot-4"
    }
  ],
  "ambiguous_gestures": [...],
  "discarded_marks": [...]
}
```

Log any `freeform` shapes to `base/ops/skills/shape-observations.md` for pattern
accumulation.

---

## Quality gates

- [ ] No zero-size or sub-10px strokes in `gestures` output
- [ ] Grouped gestures make spatial and temporal sense as a unit
- [ ] `css_selector` populated where a non-canvas click event exists within ±1000ms
- [ ] `freeform` gestures logged to shape-observations.md
- [ ] No transcript or verbal content referenced in this skill's output
