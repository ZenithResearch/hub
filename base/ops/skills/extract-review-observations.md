---
title: "Extract review observations"
doc_type: skills
tags: [review, observations, gestures, talking-points]
---

# Extract review observations

## Purpose

Produce a list of typed observations from the annotated transcript and events JSON.
Each observation pairs what was said (a talking point) with what was shown (a gesture).
This is the input to the review document writer.

---

## Inputs

From process state:
- `resolved_transcript` — Pass 4 annotated transcript (from `annotate-review-transcript`)
- `words` — with event-timeline timestamps
- `events` — full event array

---

## Execution

### 1. Extract talking points

Segment the transcript into distinct talking points by topic shifts and pauses
(> 1.5s between words). Classify each as `observation`, `request`, `question`,
`approval`, `pointing`, or `fragment`.

### 2. Reconstruct gestures

Parse strokes from the events JSON. Filter degenerate marks (zero-size, < 10px path,
< 80ms duration). Classify each stroke by shape. Group consecutive strokes within
1000ms and overlapping bounds into single gestures. Extract CSS selectors from nearby
click events.

### 3. Correlate screenshots

For each gesture, resolve `screenshot_ref_id` to an asset path using
`screenshot-captured` events. Fall back to `bounds_and_target` if no screenshot exists.

### 4. Merge

Match each talking point to gestures within ±3s timestamp window. Filter out
`approval`, `pointing`, and `fragment` talking points. Unmatched gestures become
silent annotations.

---

## Output

```json
{
  "observations": [
    {
      "observation_id": "obs-1",
      "type": "request",
      "talking_point": { "text": "just call these", "start_ms": 31000, "end_ms": 33500 },
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
    }
  ],
  "silent_annotations": [
    { "gesture_id": "g-5", "shape": "circle", "bounds": {...}, "elapsed_start_ms": 9800 }
  ],
  "filtered_points": [
    { "text": "fine like review now", "type": "approval" }
  ]
}
```

---

## Quality gates

- [ ] Every observation has a `talking_point` — gestures alone never create observations
- [ ] `approval`, `pointing`, and `fragment` talking points are in `filtered_points`
- [ ] `silent_annotations` contains all unmatched gestures
- [ ] Gesture timestamps are in event timeline milliseconds (same reference as talking points)
