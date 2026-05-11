---
title: "Synthesize review feedback"
doc_type: skills
tags: [review, feedback, synthesis, acceptance-criteria]
---

# Synthesize review feedback

## Purpose

**Input:** `verbal_segments` and `silent_stubs` from process state.

**Output:** A list of `feedback_points` — authoritative, typed claims with acceptance
criteria — plus any `unresolved_stubs`. This output feeds the final document-writing
step. It does not create issues.

---

## Instructions

### 1. Resolve silent stubs

For each stub in `silent_stubs`, attempt to match it to the nearest verbal segment:

- **Timestamp match:** gap between stub's `elapsed_start_ms` and the nearest segment boundary < 3000ms → fold into that segment's `strokes` list
- **Spatial match:** stub's bounds overlap > 30% area with any stroke in any segment → fold into that segment
- **Unresolved:** if neither match succeeds, add to `unresolved_stubs` in output

---

### 2. Evaluate each verbal segment

For each segment, decide whether it produces a feedback point.

**The threshold:** the verbal cue must be specific enough to support a complete,
non-hedged claim. Ask: "Can I write a one-sentence statement about what is wrong,
missing, or needs to change — without guessing?" If yes, proceed. If not, skip this
segment — it produces no feedback point and does not appear in the output.

Fragmentary cues that trail off, filler words without a specific referent, and
sounds-like-speech but unclear content all fail the threshold. Do not produce speculative
feedback points.

---

### 3. For each qualifying segment, produce one feedback point

**a. Write the claim**

The claim is the authoritative statement of what is wrong, missing, or to be improved.
It must stand alone — no reference to the reviewer, no hedging language.

Good: "The notification indicator is not tappable."
Good: "Navigation labels in the sidebar are absent."
Bad: "Reviewer flagged the notification dot as possibly interactive."
Bad: "The copy in this section may need differentiation."

Derive the claim from the verbal cue as the primary signal, sharpened by the element
context (CSS selector, bounds, shape type).

**b. Classify the type**

| Type | Signal in verbal content |
|---|---|
| `bug` | Broken, absent, not working — "it doesn't", "they don't have it", "nothing happens" |
| `improvement` | Works but needs to be better — "just call these", "should say", "needs more" |
| `feature_request` | New capability requested — "I want to be able to", "could we add" |
| `design_feedback` | Visual/layout observation, no specific fix direction yet |
| `question` | Expressed uncertainty — the question is the feedback point |

Prefer a more specific type when the verbal content supports it. `design_feedback` is
the fallback for valid claims that can't yet be sharpened to an improvement or bug.

**c. Identify the target element**

Use the best available signal:
- CSS selector from click events (most specific)
- Shape + bounds as positional description (fallback)
- Multiple strokes in the segment → list all target elements

**d. Write acceptance criteria**

Acceptance criteria answer: what must be true when this feedback point is addressed?
Write each criterion as a testable, imperative statement. Two to four criteria per point.

Derive from the verbal claim + element context. Match specificity to what's known:
- If CSS selector is known: reference it explicitly
- If only bounds are known: describe the region
- If behavior was described: define the expected behavior precisely

Good AC: "button.zn-button--primary contrast ratio ≥ 4.5:1 against its background"
Good AC: "Each left sidebar nav item has a visible, distinct text label"
Bad AC: "The button looks better"
Bad AC: "The issue is resolved"

---

### 4. Assemble output

```json
{
  "feedback_points": [
    {
      "point_id": "seg-1",
      "type": "improvement",
      "claim": "The primary action button lacks sufficient contrast.",
      "verbal_cue": "this button needs more contrast",
      "target": {
        "css_selector": "button.zn-button--primary",
        "bounds": { "x": 155, "y": 81, "width": 278, "height": 194 },
        "shape": "circle",
        "screenshot_path": null,
        "spatial_source": "bounds_and_target"
      },
      "acceptance_criteria": [
        "button.zn-button--primary meets WCAG AA contrast ratio (4.5:1 for text, 3:1 for large text) against its background",
        "Contrast verified in both default and hover/focus states"
      ]
    }
  ],
  "unresolved_stubs": [
    {
      "stroke_id": "stroke-4",
      "shape": "circle",
      "bounds": { "x": 24, "y": 82, "width": 24, "height": 29 },
      "screenshot_path": null,
      "note": "Silent annotation — no verbal cue, no nearby segment match"
    }
  ]
}
```

---

## Quality gates

Before emitting output:
- [ ] Every `claim` is a complete sentence stating what is wrong/missing/to improve — no hedging
- [ ] No `claim` references "the reviewer" or uses "may", "possibly", "seems"
- [ ] Every feedback point has at least 2 acceptance criteria
- [ ] Acceptance criteria are specific and testable — not "looks better" or "is resolved"
- [ ] Segments with fragmentary or incomplete verbal cues produced no feedback point
- [ ] `type` is the most specific classification the verbal content supports
