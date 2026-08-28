# Frank Service

Frank is the Hub execution/controller service for native case pipelines. It consumes queue/eventbus work, coordinates with Cases, calls STT for review audio, writes artifacts, and performs review status writeback through Gateway.

## Runtime entrypoint

- Compose service: `frank`
- Source package: `services/frank/`
- Entrypoint: `python -m services.frank.main`
- Runtime mode: `native_case_pipeline`, the only supported Frank execution path.

## Connected services

- `queue` for work claims and settlement.
- `eventbus` for wakeups.
- `cases` for durable case state, runs, steps, slots, logs, artifacts.
- `stt-http` or managed STT provider for transcript generation.
- `gateway-http` for review status writeback and HubFS/admin calls.
- `.hermes/frank_execution` for local execution artifacts.

## Main source files

- `main.py` — service loop, event handling, native-pipeline scheduling.
- `case_pipeline_runner.py` — native review/case step execution.
- `stt_client.py` — STT provider boundary and local fallback client.
- `review_packet.py` — review packet assembly/quality logic.
- `review_case_automaton.py` — review status automaton.

## Current docs

- `../../docs/frank-native-case-pipeline.md` — authoritative native pipeline overview.
- `../../docs/operations/review-case-automaton.md` — Step 8 status automaton.
- `../../docs/ops/elevenlabs-stt-rollout.md` — STT provider rollout notes.

## Focused verification

```bash
uv run pytest tests/test_frank_case_pipeline_runner.py tests/test_frank_dispatcher.py tests/test_frank_stt_client.py -q
uv run pytest tests/test_review_packet.py tests/test_review_case_automaton.py -q
```
