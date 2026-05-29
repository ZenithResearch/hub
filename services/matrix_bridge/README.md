# Matrix Bridge Service

Matrix Bridge receives Hub-facing bridge calls and posts/coordinates messages with the local Matrix/Synapse community surfaces.

## Runtime entrypoint

- Compose service: `matrix-bridge`
- Source package: `services/matrix_bridge/`
- Entrypoint: `python -m services.matrix_bridge.main`
- Default local port: `8084`

## Connected services

- Synapse (`matrix-synapse`) for Matrix rooms/users.
- Queue and Eventbus for converting Matrix-visible events into Hub work.
- `scripts/setup_matrix_bots.sh` and `infra/matrix/README.md` own token/appservice setup.

## Main source files

- `main.py` — FastAPI bridge service.

## Current docs

- `../../infra/matrix/README.md` — Synapse and appservice registration.
- `../ingest/README.md` — Matrix ingestion loop.
