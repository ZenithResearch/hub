# Process Indexer Service

Process Indexer indexes process definitions from `base/ops/processes` into the Hub knowledge/vector layer so runtime services and operators can retrieve process context.

## Runtime entrypoint

- Compose service: `process-indexer`
- Source package: `services/process_indexer/`
- Entrypoint: `python -m services.process_indexer.main`
- Restart policy in compose: `no`

## Connected services

- Qdrant for vector/index storage.
- `base/ops/processes` as the source directory.
- Runtime gRPC and Frank consume the resulting process context indirectly.

## Main source files

- `main.py` — process indexing worker.

## Current docs

- `../../docs/README.md` for current vs historical docs boundaries.
