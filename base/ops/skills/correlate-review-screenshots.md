---
title: "Correlate review screenshots"
doc_type: skills
tags: [review, screenshots, vision, spatial, correlation]
---

# Correlate review screenshots

## Purpose

Attach spatial evidence to each timeline segment. Prefer screenshot assets (for
vision model input) but fall back to CSS selector + bounds when no screenshot
exists — this avoids unnecessary vision model calls on highlight and selection
segments.

## Instructions

### 1. Build a screenshot asset map

From the review's asset list, find all assets with `asset_type: screenshot`.
Build a map: `{ screenshot_id → asset_id }` using the `screenshot-captured`
events in the events JSON:

```
events where type == "screenshot-captured":
  screenshot_map[event.screenshotId] = resolve_asset_id(event)
```

Note: the screenshot asset is identified by matching the `screenshotId` in the
event to the asset's metadata. The asset file lives at
`data/reviews/assets/{asset_id}`.

### 2. Attach spatial reference per segment

For each segment in process state `timeline`:

**If `segment.screenshot_ref_id` is set AND exists in `screenshot_map`:**
- Set `segment.screenshot_path = data/reviews/assets/{asset_id}`
- Set `segment.spatial_source = "screenshot"`
- This segment will be passed to a vision model in the synthesis step.

**Otherwise (highlight, selection, or stroke with no screenshot):**
- Set `segment.screenshot_path = null`
- Set `segment.spatial_source = "bounds_and_target"`
- The `bounds` and `target` fields are sufficient spatial context.
- Do NOT call a vision model for this segment — use bounds + target CSS selector
  as the spatial description instead.

### 3. Cost note

Vision model calls are expensive. Only segments with `spatial_source: "screenshot"`
should be sent to a vision model. Segments with `spatial_source: "bounds_and_target"`
should be described positionally: "the element matching `{target}` at coordinates
({bounds.x}, {bounds.y}), size {bounds.width}×{bounds.height}px."

### 4. Update process state

Write the enriched `timeline` array back to process state with `screenshot_path`
and `spatial_source` fields added to each segment.
