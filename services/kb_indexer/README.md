# KB Indexer Service

KB Indexer is a one-shot/indexing worker that seeds or refreshes knowledge-base documents into Qdrant for runtime search.

## Runtime entrypoint

- Compose service: `kb-indexer`
- Source package: `services/kb_indexer/`
- Entrypoint: `python -m services.kb_indexer.main`
- Restart policy in compose: `no`

## Connected services

- Qdrant for vector storage.
- `kb_seed` / configured seed directories for source documents.
- Runtime gRPC for downstream search consumption.

## Main source files

- `main.py` — indexer implementation and CLI-style entrypoint.

## Focused verification

Run targeted runtime/search tests when changing shared KB contracts; otherwise verify compose config after env changes.
