---
title: "Merge talking points and gestures"
doc_type: skills
tags: [review, merge, talking-points, gestures, synthesis]
---

# Merge talking points and gestures

## Purpose

Combine the transcript talking points (from `extract-talking-points`) and the
spatial gestures (from `reconstruct-review-narrative`) into a unified list of
**review observations** — each one pairing what was said with what was shown.

Talking points are the primary signal. Gestures provide element context.
A talking point without a matching gesture is still a valid observation.
A gesture without a matching talking point is a silent annotation.

---

## Instructions

### 1. Match by timestamp proximity

For each talking point (`start_ms` → `end_ms`), find all gestures whose
`elapsed_start_ms` falls within the window:
`[talking_point.start_ms - 3000, talking_point.end_ms + 3000]`

A 3-second window on each side accounts for the reviewer starting to draw
slightly before or after speaking.

If multiple gestures match one talking point, include all of them — they are
all part of the same observation.

If one gesture matches multiple talking points (overlapping windows), assign
it to the talking point whose window center is closest to the gesture's
`elapsed_start_ms`.

---

### 2. Classify unmatched items

- **Talking point with no gesture match** → `verbal_only` observation. Still
  valid. The talking point is the full observation; element context is unknown
  or general ("whole screen / page").

- **Gesture with no talking point match** → `silent_annotation`. Carry forward
  as an unresolved annotation. Do not produce a review issue from it.

---

### 3. Filter non-actionable talking points

Talking points of type `approval`, `pointing`, and `fragment` do not produce
observations regardless of whether they have a gesture match. Move them to
`filtered_points`.

---

### 4. Assemble output

```json
{
  "observations": [
    {
      "observation_id": "obs-1",
      "talking_point": {
        "point_id": "tp-1",
        "text": "just call these",
        "type": "request",
        "start_ms": 31000,
        "end_ms": 33500
      },
      "gestures": [
        {
          "gesture_id": "g-2",
          "shape": "bracket",
          "bounds": { "x": 36, "y": 105, "width": 160, "height": 76 },
          "css_selector": null,
          "screenshot_path": null
        }
      ],
      "match_type": "verbal_and_gesture"
    },
    {
      "observation_id": "obs-2",
      "talking_point": {
        "point_id": "tp-3",
        "text": "maybe not the canvas",
        "type": "question",
        "start_ms": 28000,
        "end_ms": 30000
      },
      "gestures": [],
      "match_type": "verbal_only"
    }
  ],
  "silent_annotations": [
    {
      "gesture_id": "g-5",
      "shape": "circle",
      "bounds": { "x": 4, "y": 10, "width": 36, "height": 25 },
      "elapsed_start_ms": 9800
    }
  ],
  "filtered_points": [
    { "point_id": "tp-4", "text": "fine like review now", "type": "approval" }
  ]
}
```

---

## Quality gates

- [ ] Every observation has a `talking_point` — gestures alone never create observations
- [ ] `silent_annotations` contains all unmatched gestures
- [ ] `approval`, `pointing`, and `fragment` talking points are in `filtered_points`
- [ ] Gesture timestamps are in event timeline milliseconds (same reference as talking points)
