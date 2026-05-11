---
title: "Transcribe review audio"
doc_type: skills
tags: [review, audio, transcription, local-stt, whisper]
---

# Transcribe review audio

## Purpose

Produce a verbatim, time-stamped transcript of the review audio. No
interpretation, no inference, no connection to shapes or events. Output is
the spoken words and when each word was said — nothing more.

---

## Instructions

### 1. Locate the audio asset

From the review record, find the asset with `asset_type: "audio"`.
File path: `data/reviews/assets/{asset_id}` or the case runtime materialized
asset path. Confirm the file is readable and is an audio/webm, mp3, wav, or m4a
asset.

### 2. Call local Whisper STT

Preferred contract surface:

```
tool: local_whisper
input:
  audio_path: <absolute path to asset file>
```

If the worker runtime does not expose `local_whisper` as a first-class callable,
do not block just because the command is absent from `PATH`. In the Frank Kanban
compose runtime, `local_whisper` is a registry-backed Python tool at
`/hub/libs/tools/local_whisper/tool.py` that forwards to the internal STT HTTP
service. Use this fallback from `/hub`:

```python
from libs.tools.local_whisper import tool
result = tool.run({"audio_path": audio_path})
```

Or call the same compose-local service directly:

```python
import httpx, os
url = os.environ.get("STT_HTTP_URL", "http://stt-http:8765").rstrip("/") + "/transcribe"
result = httpx.post(url, json={"audio_path": audio_path, "model": "tiny"}, timeout=300.0).json()
```

Do not install Whisper/Torch inside the worker container. The heavy dependency
lives in the `stt-http` service; the worker only calls the registry tool or HTTP
adapter.

Do not pass `language` unless the review record explicitly supplies one; let the
model auto-detect by default.

### 3. Compute audio_offset_ms

Find the first `audio-chunk` event in the events JSON. Read its `elapsedMs`.
This is `audio_offset_ms` — when audio recording started within the review
session. All word timestamps from local Whisper STT are relative to audio file start;
this offset aligns them to the shared event timeline.

If the events JSON has no `audio-chunk` event, use the first word timestamp as
the audio offset if it is clearly delayed; otherwise use `0`.

`event_timeline_ms = word.start_s × 1000 + audio_offset_ms`

### 4. Build the word list

Use local Whisper word timestamp output. Apply the offset to every word's start
and end:

```json
{ "text": "this", "start_ms": 5340, "end_ms": 5520 }
```

### 5. Output

```json
{
  "transcript": "verbatim full text as a single string",
  "audio_offset_ms": 5200,
  "words": [
    { "text": "this",   "start_ms": 5340, "end_ms": 5520 },
    { "text": "button", "start_ms": 5540, "end_ms": 5780 }
  ]
}
```

`transcript` is the full text joined verbatim — no corrections or paraphrasing.
`words` ordered by `start_ms`.

### 6. Handle failure

If local Whisper STT returns an error or empty transcript: fail the step explicitly
with the provider error and do not fabricate transcript output. Empty outputs are
only valid when the audio is verifiably silent.

---

## Quality gates

- [ ] `words` contains word tokens with `text`, `start_ms`, and `end_ms`
- [ ] All timestamps converted to event timeline milliseconds (offset applied)
- [ ] `transcript` is verbatim — no corrections, summaries, or edits
- [ ] No shape, stroke, or element reasoning — that is downstream
