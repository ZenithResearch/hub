# Vault API Service

Vault API exposes a configured vault through authenticated HTTP routes. It is a local/controlled integration surface for reading vault content without treating the vault as part of the Hub source tree.

## Runtime entrypoint

- Source package: `services/vault_api/`
- Entrypoint: `python -m services.vault_api.main`

## Connected services

- Configured vault root and auth/session settings.
- Optional upstream auth validation through Hub/Gateway depending on deployment profile.

## Main source files

- `main.py` — uvicorn entrypoint.
- `app.py` — FastAPI app factory and routes.
- `auth.py` — auth helpers.
- `config.py` — env-backed configuration.
- `scanner.py` — vault file scanning.

## Current docs

- `../../docs/local-runtime-state.md` — local/private data boundary.
