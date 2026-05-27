# ElevenLabs Scribe v2 STT rollout

## Purpose

Move production review transcription from always-on local Whisper `tiny` to managed ElevenLabs Scribe v2 batch while preserving local Whisper as fallback.

Audio isolation is a planned optional preprocessing step for noisy review recordings. It is not part of the baseline production switch and should remain off until a real audio sample comparison shows enough transcript-quality gain to justify the added vendor call, latency, cost, and failure mode.

## Cost expectation

Expected early volume is 10-30 audio-hours/month.

| Usage | ElevenLabs Scribe v1/v2 batch at $0.22/hr | Current local Fargate STT |
|---:|---:|---:|
| 10 hr/mo | $2.20/mo | $36.04/mo fixed |
| 30 hr/mo | $6.60/mo | $36.04/mo fixed |

Audio isolation is an additional pass over the audio. If it is priced near the STT per-minute rate, enabling it globally would roughly double transcription-side usage cost. If it is priced at half the STT rate, it would add roughly 50%. Keep it opt-in until quality evidence justifies the increase.

## Required production configuration

```text
STT_PROVIDER=elevenlabs
STT_MODEL=scribe_v2
STT_FALLBACK_PROVIDER=local_whisper
STT_AUDIO_PREPROCESSOR=none
ELEVENLABS_API_KEY=<secret-backed value>
```

Optional later, for noisy review audio only:

```text
STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation
```

API key guidance:

- Use a dedicated environment-specific ElevenLabs key such as `hub-frank-stt-prod`; do not reuse an overarching workspace key.
- Grant Speech-to-Text / Scribe permissions.
- Grant audio isolation / audio processing permissions only if `STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation` is enabled.
- Do not grant text-to-speech, voice cloning, voice-library management, agents, billing, workspace administration, API-key management, or unrelated write/delete permissions.

Keep local fallback configured:

```text
STT_HTTP_URL=http://stt-http.zenith-hub-prod.local:8765
stt_http_desired_count=1
```

## Privacy note

Review audio is sent to ElevenLabs when `STT_PROVIDER=elevenlabs`. Do not use this provider for sensitive customer/private audio until the operator/user-facing data-processing posture is accepted.

Follow-up product note: `ZenithOS Hub should let users choose STT strategy` in the operator vault.

## Smoke test

1. Submit a short real review with audio.
2. Verify the case reaches Step 2 completion.
3. Read the generated transcript artifact.
4. Confirm:

```json
{
  "provider": "elevenlabs",
  "model": "scribe_v2",
  "audio_preprocessor": "none"
}
```

5. Confirm downstream review packet has actionable transcript segments.

If audio isolation is tested, run the same review sample once with `STT_AUDIO_PREPROCESSOR=none` and once with `STT_AUDIO_PREPROCESSOR=elevenlabs_audio_isolation`, then compare transcript quality before changing the default.

## Rollback

1. Set `STT_PROVIDER=local_whisper`.
2. Redeploy Frank only.
3. Keep `stt-http` desired count at `1` until the rollback window closes.
4. Verify Step 2 transcript artifact changes back to `provider=local_whisper`.

## Rollout scope

Expected production deployment scope:

- Frank image containing `services/frank/stt_client.py`.
- Frank task definition env update.
- Frank `ELEVENLABS_API_KEY` secret injection.
- Baseline `STT_AUDIO_PREPROCESSOR=none` env update.
- Execution-role secret read permission if secret injection uses Secrets Manager.

Do not roll Gateway/Cases/Eventbus/STT images unless the plan explicitly includes them.
