# Cases Service

Cases is the durable state service for Hub work. It owns cases, process contracts, steps, slots, logs, execution runs, step runs, spans, events, artifacts, and streaming inspection APIs used by Frank and ZenithOS.

## Runtime entrypoint

- Compose service: `cases`
- Source package: `services/cases/`
- Entrypoint: `uvicorn services.cases.main:app_instance --host 0.0.0.0 --port ${CASES_HTTP_PORT:-8083}`
- Default local port: `8083`

## Connected services

- Frank creates/updates cases and execution records.
- Gateway exposes selected admin/operator case routes.
- Hermes worker queue can reconcile outputs through Cases.
- ZenithOS should treat Cases APIs as the source of truth for process/case inspection.

## Main source files

- `main.py` — FastAPI service, storage, case/run/step/artifact APIs.
- `contract.py` — process contract parsing and validation.

## Current docs

- `../../docs/frank-native-case-pipeline.md` — native pipeline and observability API contract.
- `../../docs/case-dispatch-review.md` — case dispatch review notes.
- `../../docs/operations/review-case-automaton.md` — review-specific terminal status semantics.

## Focused verification

```bash
uv run pytest tests/test_cases_contract.py tests/test_cases_observability.py tests/test_process_contract.py -q
```

## Legacy containment

Native review execution uses the fenced claim protocol and immutable attempt identities described in [legacy review containment](../../docs/operations/legacy-review-containment.md). Invalid historical contracts remain inspectable but cannot execute. These are legacy migration records, not the new process-runtime or service-management aggregates.

Run `python -m pytest tests/test_legacy_containment.py -q` with the focused suites above for concurrency, crash and restart coverage.
