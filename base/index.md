---
title: Base
type: moc
description: "Top-level MOC for the hub's base knowledge layer — processes, skills, and foundational content indexed for Frank's RAG"
---

# Base

The `base/` directory is the hub's foundational knowledge layer. It is indexed into
Qdrant and searched by Frank during intent recognition and process dispatch.

Content here falls into directories that mirror the vault's knowledge domains. Each
subdirectory has its own `index.md` MOC. The `index.md` files define structure and
are not indexed as searchable content — only documents in subdirectories are indexed.

The vault can push processed content here via `VaultWriteService` (ISS-054). This is
the landing zone for knowledge that has moved from `capture/` through the vault's
processing pipeline and is ready for hub consumption.

## Directories

- [processes/](processes/index.md) — process definitions; Frank's primary dispatch source

## What belongs here vs. spaces/

| Put it in `base/` | Put it in `spaces/` |
|---|---|
| Processes Frank dispatches | Client-facing API surfaces |
| Shared skills and capabilities | Tenant-scoped content |
| Foundational reference docs | Space-specific workflows |
| Vault-pushed processed knowledge | Space-owned integrations |
