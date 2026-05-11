---
title: "Extract talking points from review transcript"
doc_type: skills
tags: [review, transcript, talking-points, language]
---

# Extract talking points from review transcript

## Purpose

Read the time-stamped transcript and identify the distinct things the reviewer
was talking about. Output is a list of talking points — each one a coherent
statement, observation, or question — with the time range it was spoken. No
shapes, no events, no element references at this stage. This is pure language
analysis.

---

## Instructions

### 1. Read the transcript

Input: `transcript` (full string) and `words` (time-stamped word list) from
process state.

---

### 2. Identify natural statement boundaries

Read the full transcript as speech. Segment it into talking points by listening
for:

- **Topic shifts** — the reviewer moves from one observation to another
- **Sentence-final intonation markers** — words like "right", "okay", "so",
  "anyway", "and" at the start of a new thought
- **Hesitations or restarts** — "I... this X" is one point; "maybe not the
  canvas... the canvas ready version" is one point
- **Pauses** — a gap of > 1.5s between words (detectable from timestamps)
  typically marks a boundary between talking points

Do not split on every sentence. A talking point is a complete thought, even if
expressed across multiple sentences. "Just call... just call these" is one
talking point (a repetition for emphasis). "And then you can... and highlight
and be like" is one fragmented talking point.

---

### 3. Classify each talking point

| Type | Signal |
|------|--------|
| `observation` | Describes something they see — "it has this X" |
| `request` | Asks for something — "just call these", "to be able to put a CTA" |
| `question` | Expresses uncertainty — "maybe not the canvas?" |
| `approval` | Positive validation — "fine, like review now" |
| `pointing` | Pure locating speech with no claim — "right here, over here" |
| `fragment` | Incomplete sentence with no recoverable meaning |

`fragment` and `pointing` talking points will not produce issues — flag them
but include them in output for completeness.

---

### 4. Determine time range

For each talking point, find the `start_ms` of its first word and the `end_ms`
of its last word from the `words` array.

---

### 5. Store in process state

```json
{
  "talking_points": [
    {
      "point_id": "tp-1",
      "text": "just call these",
      "type": "request",
      "start_ms": 31000,
      "end_ms": 33500
    },
    {
      "point_id": "tp-2",
      "text": "they don't have it",
      "type": "observation",
      "start_ms": 35000,
      "end_ms": 36300
    },
    {
      "point_id": "tp-3",
      "text": "right here over here",
      "type": "pointing",
      "start_ms": 10200,
      "end_ms": 16100
    }
  ]
}
```

Order by `start_ms`.

---

## Quality gates

- [ ] No talking point contains stroke or shape references — this is transcript only
- [ ] Fragment and pointing talking points are included but flagged as their type
- [ ] Each talking point has a recoverable time range from the `words` array
- [ ] Adjacent words that form one continuous thought are not split into separate points
