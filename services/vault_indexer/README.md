# Vault Indexer Service

Vault Indexer contains indexing helpers for vault-backed knowledge surfaces. It should treat the vault as external/private input, not as source files to commit into Hub.

## Runtime entrypoint

- Source package: `services/vault_indexer/`
- Primary module: `indexer.py`

## Connected services

- Vault files/directories supplied by local configuration.
- Vector/search infrastructure when wired into a runtime profile.

## Main source files

- `indexer.py` — vault indexing logic.

## Current docs

- `../../docs/local-runtime-state.md` — local/private runtime state boundary.
