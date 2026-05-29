# Matrix Ingest Service

Ingest watches Matrix/Synapse rooms as Sophia/local bot users and normalizes inbound messages into Hub queue/eventbus work.

## Runtime entrypoint

- Compose service: `ingest`
- Source package: `services/ingest/`
- Entrypoint: `python -m services.ingest.main`

## Connected services

- Synapse (`matrix-synapse`) as Matrix homeserver.
- Queue HTTP for enqueueing normalized work.
- Eventbus for wakeups.

## Main source files

- `main.py` — ingest service runner.
- `matrix_client.py` — Matrix client wrapper.
- `normalizer.py` — inbound message normalization.
- `appservice.py` — appservice-related helpers.
- `config.py` — env-backed ingest settings.

## Current docs

- `../../infra/matrix/README.md` — local Synapse/appservice setup.
- `../matrix_bridge/README.md` — outbound/bridge service.
