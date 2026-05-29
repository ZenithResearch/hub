# STT HTTP Service

STT HTTP is the local Whisper transcription service and production fallback target for review audio. It accepts allowed local/container file paths and returns transcript text/metadata to Frank.

## Runtime entrypoint

- Compose service: `stt-http`
- Source package: `services/stt_http/`
- Entrypoint: Dockerfile `docker/stt_http/Dockerfile`, FastAPI app in `services.stt_http.main:app`
- Default local port: `8765`

## Connected services

- Frank calls STT HTTP through `STT_HTTP_URL` when using local Whisper or fallback.
- Review asset data is mounted read-only from `reviews_data` and `.hermes/frank_execution`.
- Production may use managed ElevenLabs first and retain this service as fallback.

## Main source files

- `main.py` — FastAPI app, allowed-root checks, Whisper model cache, transcription endpoint.

## Current docs

- `../../docs/ops/elevenlabs-stt-rollout.md` — managed STT/fallback rollout posture.
- `../frank/README.md` — Frank STT provider boundary.

## Focused verification

```bash
uv run pytest tests/test_stt_http_service.py tests/test_frank_stt_client.py -q
```
