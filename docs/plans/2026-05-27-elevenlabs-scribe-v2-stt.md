# ElevenLabs Scribe v2 Batch STT Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make ElevenLabs Scribe v2 batch the primary STT provider for native review cases, while preserving local Whisper as a fallback and leaving room for optional audio isolation when review recordings are noisy.

**Architecture:** Introduce a provider boundary inside Frank Step 2 instead of hard-coding `stt-http`. The case pipeline should call a common STT client that returns the existing transcript payload shape: `transcript`, `words`, `language_code`, `model`, plus provider metadata. ElevenLabs Scribe v2 batch becomes the production default; local `stt-http` remains a fallback and local-dev option. Audio isolation/enhancement is modeled as an optional preprocessing step before STT, not as a mandatory part of baseline transcription.

**Tech Stack:** Python, httpx, Frank native case pipeline, AWS ECS/Fargate, Terraform, ElevenLabs Speech-to-Text API.

---

## Current state

- Deployed production STT is local CPU Whisper via `stt-http`.
- Current exact deployed local model: `STT_WHISPER_MODEL=tiny`.
- Current local allowlist: `tiny,base,small`.
- Current fixed STT hosting cost: about `$36.04/month` for 1 vCPU / 2 GB Fargate.
- Expected review volume: 10-30 audio-hours/month.
- ElevenLabs Scribe v1/v2 batch price: `$0.22/audio-hour`.
- Expected ElevenLabs monthly cost at target volume:
  - 10 hr/mo: `$2.20`
  - 30 hr/mo: `$6.60`
- Audio isolation is an optional extra audio pass. If priced at roughly the same per-minute rate as STT, enabling it globally would approximately double transcription-side usage cost; if priced at 0.5x STT it would add about 50%. Do not assume it is free in production budgeting.

## Non-goals

- Do not implement realtime transcription in this pass.
- Do not remove `stt-http` or the local Whisper service yet.
- Do not build GPU self-hosting for Voxtral/Parakeet yet.
- Do not print or persist raw ElevenLabs API keys in logs, tests, artifacts, or docs.
- Do not change review packet schema incompatibly; additive provider metadata only.
- Do not turn audio isolation on globally in the initial production rollout; ship baseline Scribe v2 first and enable isolation selectively after real noisy-review samples justify the added cost, latency, and failure mode.

## Provider contract

All STT providers must return this internal shape:

```python
{
    "transcript": "spoken text",
    "words": [
        {"text": "spoken", "start": 0.0, "end": 0.4, "type": "word"}
    ],
    "language_code": "en",
    "model": "scribe_v2",
    "provider": "elevenlabs",
}
```

Frank continues to normalize words into `start_ms` / `end_ms` with existing `normalize_words` logic.

## Environment contract

Add these production env vars on Frank:

```text
STT_PROVIDER=elevenlabs
STT_MODEL=scribe_v2
STT_FALLBACK_PROVIDER=local_whisper
STT_AUDIO_PREPROCESSOR=none
ELEVENLABS_API_KEY=<secret value from AWS Secrets Manager or approved secret injection path>
```

Keep existing local fallback env:

```text
STT_HTTP_URL=http://stt-http.zenith-hub-prod.local:8765
```

Recommended local-dev default:

```text
STT_PROVIDER=local_whisper
STT_AUDIO_PREPROCESSOR=none
```

Optional noisy-audio rollout setting after baseline smoke tests pass:

```text
STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation
```

Use a dedicated environment-specific ElevenLabs key, not one overarching workspace key. Minimum desired key scope:

- Speech-to-text / Scribe access.
- Audio isolation / audio processing access only if `STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation` is enabled.
- No text-to-speech, voice cloning, voice library management, agents, billing, workspace administration, API-key management, or unrelated write/delete permissions.

---

## Task 1: Add an STT provider client module

**Objective:** Create a single Frank-owned STT client abstraction that can route to ElevenLabs or local Whisper.

**Files:**
- Create: `services/frank/stt_client.py`
- Test: `tests/test_frank_stt_client.py`

**Step 1: Write failing tests**

Create `tests/test_frank_stt_client.py` with tests for:

1. `provider_from_env()` defaults to `local_whisper` when `STT_PROVIDER` is unset.
2. `provider_from_env()` reads `STT_PROVIDER=elevenlabs`.
3. `normalize_provider_payload()` accepts ElevenLabs response shape and returns internal shape.
4. Missing `ELEVENLABS_API_KEY` raises a clear runtime error only when the selected provider is ElevenLabs.
5. Local provider posts to `{STT_HTTP_URL}/transcribe` exactly as current behavior does.

**Step 2: Run failing tests**

```bash
cd <hub repo>
pytest tests/test_frank_stt_client.py -q
```

Expected: FAIL because `services.frank.stt_client` does not exist.

**Step 3: Implement module skeleton**

Create `services/frank/stt_client.py` with:

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_LOCAL_STT_URL = "http://stt-http:8765"
DEFAULT_LOCAL_MODEL = "tiny"
DEFAULT_ELEVENLABS_MODEL = "scribe_v2"


def selected_provider() -> str:
    return (os.environ.get("STT_PROVIDER") or "local_whisper").strip().lower() or "local_whisper"


def selected_model(provider: str) -> str:
    if provider == "elevenlabs":
        return (os.environ.get("STT_MODEL") or DEFAULT_ELEVENLABS_MODEL).strip() or DEFAULT_ELEVENLABS_MODEL
    return (os.environ.get("LOCAL_WHISPER_MODEL") or os.environ.get("STT_MODEL") or DEFAULT_LOCAL_MODEL).strip() or DEFAULT_LOCAL_MODEL


def normalize_words(raw_words: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_words, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_words:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("word") or "").strip()
        if not text:
            continue
        start = item.get("start")
        end = item.get("end")
        if start is None:
            start = item.get("start_time")
        if end is None:
            end = item.get("end_time")
        rows.append({"text": text, "start": float(start or 0), "end": float(end or 0), "type": str(item.get("type") or "word")})
    return rows
```

Then add async provider methods:

```python
async def transcribe_audio(client: httpx.AsyncClient, audio_path: str) -> dict[str, Any]:
    provider = selected_provider()
    if provider == "elevenlabs":
        return await transcribe_elevenlabs(client, audio_path, model=selected_model(provider))
    if provider in {"local", "local_whisper", "stt-http", "stt_http"}:
        return await transcribe_local_whisper(client, audio_path, model=selected_model(provider))
    raise RuntimeError(f"unsupported STT_PROVIDER: {provider}")
```

**Step 4: Add local provider method**

Preserve current behavior:

```python
async def transcribe_local_whisper(client: httpx.AsyncClient, audio_path: str, *, model: str) -> dict[str, Any]:
    base_url = (os.environ.get("STT_HTTP_URL") or DEFAULT_LOCAL_STT_URL).rstrip("/")
    response = await client.post(f"{base_url}/transcribe", json={"audio_path": audio_path, "model": model}, timeout=180.0)
    response.raise_for_status()
    data = response.json()
    return {
        "transcript": str(data.get("transcript") or "").strip(),
        "words": normalize_words(data.get("words")),
        "language_code": str(data.get("language_code") or data.get("language") or ""),
        "model": str(data.get("model") or model),
        "provider": "local_whisper",
    }
```

**Step 5: Run tests**

```bash
pytest tests/test_frank_stt_client.py -q
```

Expected: local-provider tests pass; ElevenLabs tests still fail until Task 2.

---

## Task 2: Implement ElevenLabs Scribe v2 batch provider

**Objective:** Add a secret-safe ElevenLabs transcription implementation.

**Files:**
- Modify: `services/frank/stt_client.py`
- Test: `tests/test_frank_stt_client.py`

**Step 1: Add tests for request construction**

Test that `transcribe_elevenlabs()`:

- Reads `ELEVENLABS_API_KEY` from env.
- Sends the API key as `xi-api-key` header or current ElevenLabs-documented auth header.
- Sends `model_id=scribe_v2`.
- Uploads the audio file as multipart form data.
- Does not include the API key in raised exception text.

**Step 2: Implement provider**

Use ElevenLabs Speech-to-Text endpoint from docs. At plan time the expected endpoint is:

```text
POST https://api.elevenlabs.io/v1/speech-to-text
```

Implementation shape:

```python
async def transcribe_elevenlabs(client: httpx.AsyncClient, audio_path: str, *, model: str) -> dict[str, Any]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is required when STT_PROVIDER=elevenlabs")

    path = Path(audio_path)
    if not path.is_file():
        raise RuntimeError(f"audio file not found for ElevenLabs STT: {path.name}")

    endpoint = os.environ.get("ELEVENLABS_STT_URL", "https://api.elevenlabs.io/v1/speech-to-text")
    with path.open("rb") as audio_file:
        response = await client.post(
            endpoint,
            headers={"xi-api-key": api_key},
            data={"model_id": model},
            files={"file": (path.name, audio_file, "application/octet-stream")},
            timeout=300.0,
        )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = exc.response.text[:500] if exc.response is not None else ""
        raise RuntimeError(f"ElevenLabs STT request failed with status {status}: {body}") from exc

    data = response.json()
    return normalize_elevenlabs_response(data, model=model)
```

**Step 3: Implement response normalizer**

Accept both likely Scribe fields:

```python
def normalize_elevenlabs_response(data: dict[str, Any], *, model: str) -> dict[str, Any]:
    words = data.get("words") or []
    language_code = data.get("language_code") or data.get("language") or ""
    return {
        "transcript": str(data.get("text") or data.get("transcript") or "").strip(),
        "words": normalize_words(words),
        "language_code": str(language_code),
        "model": str(data.get("model") or model),
        "provider": "elevenlabs",
    }
```

**Step 4: Run tests**

```bash
pytest tests/test_frank_stt_client.py -q
```

Expected: PASS.

---

## Task 3: Route Frank Step 2 through the provider client

**Objective:** Replace Step 2's hard-coded `stt-http` call with provider selection while keeping fallback behavior.

**Files:**
- Modify: `services/frank/case_pipeline_runner.py`
- Test: `tests/test_frank_case_pipeline_runner.py`

**Step 1: Write tests**

Add/adjust tests proving:

1. With `STT_PROVIDER=elevenlabs`, Step 2 calls `stt_client.transcribe_audio()` rather than posting directly to `/transcribe`.
2. Step 2 artifact `transcript.json` includes `provider` and `model` metadata.
3. Existing local retry tests still pass for local fallback path.
4. If ElevenLabs provider fails and `STT_FALLBACK_PROVIDER=local_whisper`, Step 2 retries local once and emits metadata showing fallback was used.

**Step 2: Refactor runner**

In `services/frank/case_pipeline_runner.py`:

- Import `services.frank.stt_client`.
- Replace `_post_stt_transcribe()` internals with provider call.
- Keep existing retry/backoff around transient provider errors.
- Rename user-visible event messages from hard-coded `stt-http transcription` to provider-neutral `speech-to-text transcription`.

Minimal target:

```python
from services.frank import stt_client
```

Then in `_post_stt_transcribe()`:

```python
payload = await stt_client.transcribe_audio(self.client, audio_path)
```

**Step 3: Preserve transcript output contract**

Add provider metadata to `transcript_payload` but keep existing output slots unchanged:

```python
transcript_payload = {
    "transcript": str(payload.get("transcript") or ""),
    "audio_offset_ms": audio_offset_ms,
    "words": words,
    "language_code": payload.get("language_code"),
    "model": payload.get("model"),
    "provider": payload.get("provider"),
}
```

Return slots remain:

```python
return {
    "transcript": transcript_payload["transcript"],
    "audio_offset_ms": audio_offset_ms,
    "words": words,
}
```

Do not add new required process output variables in this task.

**Step 4: Run tests**

```bash
pytest tests/test_frank_case_pipeline_runner.py tests/test_frank_stt_client.py -q
```

Expected: PASS.

---

## Task 3A: Add optional audio preprocessing boundary

**Objective:** Support audio isolation as an optional pre-STT step for noisy review recordings without making it part of the baseline rollout.

**Files:**
- Modify: `services/frank/stt_client.py` or create `services/frank/audio_preprocessing.py`
- Test: `tests/test_frank_stt_client.py` or create `tests/test_frank_audio_preprocessing.py`
- Modify later if needed: `services/frank/case_pipeline_runner.py`

**Step 1: Add config and tests**

Add tests proving:

1. `STT_AUDIO_PREPROCESSOR` defaults to `none`.
2. `STT_AUDIO_PREPROCESSOR=none` sends the original audio path directly to STT.
3. `STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation` calls an isolation provider before STT and passes the processed audio artifact to transcription.
4. Audio-isolation failures fall back to original audio or fail closed according to explicit config, but never silently drop the review audio.
5. Transcript metadata records the preprocessing decision.

**Step 2: Implement preprocessor contract**

Model preprocessing independently from transcription:

```python
{
    "audio_path": "/data/.../isolated.wav",
    "audio_preprocessor": "elevenlabs_audio_isolation",
    "source_audio_path": "/data/.../review.webm",
    "processed_audio_artifact": "/data/.../isolated.wav",
}
```

Baseline `none` should return the original path and metadata only:

```python
{
    "audio_path": original_audio_path,
    "audio_preprocessor": "none",
    "source_audio_path": original_audio_path,
}
```

**Step 3: Add ElevenLabs audio isolation provider only behind config**

Use the ElevenLabs-documented audio isolation/enhancement endpoint. Requirements:

- Read the same secret-backed `ELEVENLABS_API_KEY` only from env/secret injection.
- Do not print request bodies, API keys, or signed URLs.
- Save processed audio as a separate artifact so operators can compare raw vs isolated input if policy permits.
- Keep timeout and size limits explicit; noisy long recordings should not hang Frank indefinitely.

**Step 4: Add transcript metadata**

Extend `transcript.json` additively:

```json
{
  "provider": "elevenlabs",
  "model": "scribe_v2",
  "audio_preprocessor": "none",
  "source_audio_artifact": "/data/.../review.webm"
}
```

When isolation is enabled, include:

```json
{
  "audio_preprocessor": "elevenlabs_audio_isolation",
  "source_audio_artifact": "/data/.../review.webm",
  "processed_audio_artifact": "/data/.../isolated.wav"
}
```

**Step 5: Rollout rule**

Initial production deploy remains:

```hcl
stt_audio_preprocessor = "none"
```

Only enable `elevenlabs_audio_isolation` after baseline Scribe v2 smoke tests show transcripts are blocked by noisy audio, and compare the same review samples with/without isolation before making it a default.

---

## Task 4: Update process contract docs and worker skill guidance

**Objective:** Make docs reflect provider-based STT and ElevenLabs production default without breaking local fallback.

**Files:**
- Modify: `base/ops/processes/process-queued-review.md`
- Modify: `base/ops/skills/transcribe-review-audio.md`
- Modify: `tests/test_process_contract.py`

**Step 1: Update tests first**

Change local-only tests to provider-boundary expectations:

- Process capabilities should allow `ELEVENLABS_API_KEY` for production provider.
- Keep `STT_HTTP_URL` mentioned as local fallback, not primary production dependency.
- Skill guidance should say `tool: stt_provider` or equivalent provider abstraction if the tool registry is updated; otherwise document that native Frank Step 2 owns provider selection.

**Step 2: Update docs**

Docs should state:

```text
Production STT: ElevenLabs Scribe v2 batch via Frank provider boundary.
Fallback/local STT: local Whisper through stt-http.
Required production env: ELEVENLABS_API_KEY, STT_PROVIDER=elevenlabs, STT_MODEL=scribe_v2.
```

**Step 3: Run tests**

```bash
pytest tests/test_process_contract.py -q
```

Expected: PASS.

---

## Task 5: Add Terraform/ECS configuration for ElevenLabs

**Objective:** Configure production Frank task with provider env and API key secret without exposing the secret.

**Files:**
- Modify: `infra/aws_baseline_80/variables.tf`
- Modify: `infra/aws_baseline_80/ecs.tf`
- Possibly modify: `infra/aws_baseline_80/iam.tf` if Frank task execution role needs secret read permissions.
- Test: existing Terraform validation path.

**Step 1: Add variables**

Add variables:

```hcl
variable "stt_provider" {
  description = "Frank STT provider: local_whisper or elevenlabs."
  type        = string
  default     = "local_whisper"
}

variable "stt_model" {
  description = "Selected STT model for the provider. For ElevenLabs use scribe_v2."
  type        = string
  default     = "scribe_v2"
}

variable "stt_fallback_provider" {
  description = "Optional fallback STT provider after primary provider failures."
  type        = string
  default     = "local_whisper"
}

variable "stt_audio_preprocessor" {
  description = "Optional pre-STT audio processor: none or elevenlabs_audio_isolation. Keep none for the baseline rollout."
  type        = string
  default     = "none"
}

variable "elevenlabs_api_key_secret_arn" {
  description = "AWS Secrets Manager or SSM secret ARN containing ELEVENLABS_API_KEY. Empty disables secret injection."
  type        = string
  default     = ""
}
```

**Step 2: Inject Frank env**

In Frank task `environment`, add:

```hcl
{ name = "STT_PROVIDER", value = var.stt_provider },
{ name = "STT_MODEL", value = var.stt_model },
{ name = "STT_FALLBACK_PROVIDER", value = var.stt_fallback_provider },
{ name = "STT_AUDIO_PREPROCESSOR", value = var.stt_audio_preprocessor },
```

Add conditional secret injection for `ELEVENLABS_API_KEY` without printing the value.

**Step 3: Preserve local `stt-http` service for fallback**

Do not delete `aws_ecs_task_definition.stt_http` or service yet.

Optionally set production desired count in operator tfvars later, after successful fallback evaluation:

```hcl
stt_http_desired_count = 1
```

Keep it at 1 for the first rollout so fallback exists.

**Step 4: Validate Terraform**

Use repo's existing Terraform validation command. If backend credentials are unavailable, run backendless validation only:

```bash
cd <hub repo>/infra/aws_baseline_80
terraform fmt -check
terraform init -backend=false
terraform validate
```

Expected: PASS or a named credential/backend blocker.

---

## Task 6: Add deployment/runbook documentation

**Objective:** Document rollout, smoke tests, costs, and rollback.

**Files:**
- Create or modify: `docs/ops/elevenlabs-stt-rollout.md`

**Content requirements:**

Include:

1. Expected cost:
   - 10 hr/mo = `$2.20`
   - 30 hr/mo = `$6.60`
2. Required env/secrets:
   - `STT_PROVIDER=elevenlabs`
   - `STT_MODEL=scribe_v2`
   - `STT_AUDIO_PREPROCESSOR=none` for baseline rollout
   - optional later: `STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation` for noisy audio only
   - `ELEVENLABS_API_KEY`
   - dedicated key scoped to Scribe/STT plus audio isolation only if that preprocessor is enabled
3. Smoke test:
   - Submit a short review with real audio.
   - Verify Step 2 completes.
   - Verify `transcript.json` has `provider=elevenlabs`, `model=scribe_v2`.
   - Verify review packet has actionable transcript segments.
4. Rollback:
   - Set `STT_PROVIDER=local_whisper`.
   - Redeploy Frank only.
   - Keep `stt-http` desired count at 1 until rollback window closes.
5. Privacy note:
   - Audio is sent to ElevenLabs; do not use for sensitive/private customer audio until the data-processing posture is accepted.

---

## Task 7: Production rollout plan

**Objective:** Roll only the minimum production surface after tests pass.

**Preconditions:**

- Code merged or branch selected intentionally for operator deploy.
- ElevenLabs API key stored in approved secret backend.
- Terraform plan shows Frank-only task definition/service changes, plus any secret IAM read permission required.
- `stt-http` remains running as fallback.

**Rollout steps:**

1. Build/push Gateway/Frank image containing STT provider code.
2. Run Terraform plan with:

```hcl
stt_provider = "elevenlabs"
stt_model = "scribe_v2"
stt_fallback_provider = "local_whisper"
stt_audio_preprocessor = "none"
elevenlabs_api_key_secret_arn = "<secret arn>"
```

3. Confirm plan scope:
   - Frank task definition replacement expected.
   - Frank service update expected.
   - No Gateway/Cases/Eventbus/STT image churn unless intentionally included.
4. Apply after operator approval.
5. Submit one real review case.
6. Inspect case slots/artifacts:
   - Step 2 complete.
   - Transcript present.
   - `transcript.json.provider == "elevenlabs"`.
   - `transcript.json.model == "scribe_v2"`.
7. Leave local `stt-http` enabled for at least one week.

---

## Verification commands

Focused unit tests:

```bash
cd <hub repo>
pytest tests/test_frank_stt_client.py tests/test_frank_case_pipeline_runner.py tests/test_process_contract.py -q
```

Broader review pipeline regression:

```bash
pytest tests/test_review_packet.py tests/test_frank_dispatcher.py tests/test_hermes_worker_queue.py -q
```

Terraform syntax:

```bash
cd <hub repo>/infra/aws_baseline_80
terraform fmt -check
terraform init -backend=false
terraform validate
```

Production smoke expected artifact metadata:

```json
{
  "provider": "elevenlabs",
  "model": "scribe_v2",
  "audio_preprocessor": "none"
}
```

Optional noisy-audio smoke should compare the same review sample with `audio_preprocessor=none` and `audio_preprocessor=elevenlabs_audio_isolation` before changing any default.

---

## Acceptance criteria

- Frank Step 2 can transcribe via ElevenLabs Scribe v2 batch.
- Existing local Whisper fallback still works.
- `ELEVENLABS_API_KEY` is required only for ElevenLabs provider.
- Secrets are never logged or printed.
- Review packet downstream behavior remains compatible.
- Production rollout can be scoped to Frank plus secret/IAM config.
- Baseline production rollout uses `STT_AUDIO_PREPROCESSOR=none`; audio isolation is available only as an explicit follow-up config after quality/cost comparison.
- At 10-30 audio-hours/month, expected STT usage cost is `$2.20-$6.60/month` before taxes/discounts and before any optional audio-isolation pass.
