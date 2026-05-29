# Hub Services Map

This directory contains Hub-owned runtime service packages. Each package README states what the service owns, how it connects to the rest of the stack, the local compose entrypoint, important environment variables, and focused tests.

## Runtime topology

```text
Gateway HTTP
  |-- Runtime gRPC --> Tool Sandbox
  |-- Queue ---------> Frank / Hermes Worker Queue
  |-- Eventbus ------> wakeups for Frank/workers/bridges
  |-- Cases ---------> durable case state, runs, steps, slots, logs
  |-- STT HTTP ------> local Whisper fallback for review audio
  |-- Matrix Bridge / Ingest / Feeds as intake and notification surfaces
```

## Hub-owned service packages

| Service | Package | Entrypoint | Depends on | README |
|---|---|---|---|---|
| Gateway HTTP | `services/gateway_http` | `uvicorn services.gateway_http.app:app` | runtime, queue, eventbus, clients Postgres | `gateway_http/README.md` |
| Eventbus | `services/eventbus` | `python -m services.eventbus.main` | none | `eventbus/README.md` |
| Cases | `services/cases` | `uvicorn services.cases.main:app_instance` | local/EFS/volume case DB | `cases/README.md` |
| Frank | `services/frank` | `python -m services.frank.main` | queue, eventbus, cases, stt-http | `frank/README.md` |
| STT HTTP | `services/stt_http` | `uvicorn services.stt_http.main:app` | review/frank execution file mounts | `stt_http/README.md` |
| Runtime gRPC | `services/runtime_grpc` | `python -m services.runtime_grpc.main` | tool-sandbox, qdrant | `runtime_grpc/README.md` |
| Tool Sandbox | `services/tool_sandbox` | `python -m services.tool_sandbox.main` | tool directory, optional Gateway/STT | `tool_sandbox/README.md` |
| Hermes Worker Queue | `services/hermes_worker_queue` | `python -m services.hermes_worker_queue.main` | queue, eventbus, cases, stt-http | `hermes_worker_queue/README.md` |
| Matrix Bridge | `services/matrix_bridge` | `python -m services.matrix_bridge.main` | queue, eventbus, Synapse | `matrix_bridge/README.md` |
| Matrix Ingest | `services/ingest` | `python -m services.ingest.main` | Synapse, queue, eventbus | `ingest/README.md` |
| Feeds | `services/feeds` | `python -m services.feeds.main` | eventbus, optional queue | `feeds/README.md` |
| KB Indexer | `services/kb_indexer` | `python -m services.kb_indexer.main` | qdrant | `kb_indexer/README.md` |
| Process Indexer | `services/process_indexer` | `python -m services.process_indexer.main` | qdrant, `base/ops/processes` | `process_indexer/README.md` |
| Vault API | `services/vault_api` | `python -m services.vault_api.main` | configured vault root/auth | `vault_api/README.md` |
| Vault Indexer | `services/vault_indexer` | library/indexer module | vault files, qdrant/search target | `vault_indexer/README.md` |

The queue runtime service is implemented in `inbox/`, not under `services/`; see `../inbox/README.md`.

## Backing services in compose

These are required infrastructure components but not Hub-owned Python service packages:

- `clients-postgres` — local Postgres registry for Review SDK clients/projects/deployments/access codes.
- `qdrant` — vector store for KB/process indexing and runtime search.
- `matrix-synapse` / `matrix-db` — local Matrix homeserver and database, documented under `../infra/matrix/README.md`.
- `grpcurl` — debug profile for gRPC inspection.

## Verification

Use focused tests when changing one service. Examples:

```bash
uv run pytest tests/test_gateway_http_sessions.py -q
uv run pytest tests/test_cases_contract.py tests/test_cases_observability.py -q
uv run pytest tests/test_frank_case_pipeline_runner.py tests/test_frank_dispatcher.py -q
uv run pytest tests/test_stt_http_service.py tests/test_frank_stt_client.py -q
uv run pytest tests/test_eventbus_broker.py tests/test_queue_http.py -q
```

Run `docker compose config --quiet` after changing compose-facing service docs, names, or environment examples.
