# Local runtime state and public-repo hygiene

Hub is designed so source code, contracts, templates, and generic configuration can be pushed to the public remote while real operator state stays local.

## Tracked vs untracked

Tracked and safe to push:

- source code under `services/`, `libs/`, `scripts/`, `tests/`
- generic docs under `docs/`
- generic config examples such as `.env.example`
- empty placeholders such as `data/.gitkeep`

Untracked and never safe to push:

- `.env`, `.env.*`, `.env.local`
- `.hermes/`
- local runtime databases such as `data/*.db`, `data/*.db-wal`, `data/*.db-shm`
- Matrix generated appservice registrations under `infra/matrix/appservices/*.resolved.yaml`
- local workspace repos under `repos/workspace/*`

The `.gitignore` enforces this boundary. Do not force-add ignored runtime data unless deliberately creating a sanitized fixture with no real user/client/operator data.

## Review clients registry

The Review SDK client/project/deployment/access-code registry now uses Postgres locally and in production.

Local Docker Compose starts a `clients-postgres` service and the gateway connects to it with the same split settings production uses:

```text
CLIENTS_PG_HOST=clients-postgres
CLIENTS_PG_PORT=5432
CLIENTS_PG_DATABASE=hub_clients
CLIENTS_PG_USER=hub_clients
CLIENTS_PG_PASSWORD=hub_clients_dev_password
```

Production uses private RDS Postgres with the same environment shape and injects only `CLIENTS_PG_PASSWORD` from Secrets Manager. Do not use local `data/clients.db` as an operator source of truth for live review auth.

Historical note: earlier Review SDK auth used SQLite `data/clients.db` locally and `/data/clients.db` in Docker volumes. That path is now retired for the review clients registry. Other Hub services may still use SQLite for their own local runtime state, such as queue/cases; this note is specifically about the Review SDK clients registry.

## Development rule

Push code/config/docs changes freely, but treat runtime state as local-only. If a change affects live local usage, encode the behavior as code, tests, scripts, docs, or `.env.example` defaults — never by committing resulting databases, `.env`, `.hermes`, review artifacts, or generated Matrix registrations.

Before pushing, run:

```bash
git status --short
git status --ignored --short data .hermes .env .env.local infra/matrix/appservices repos/workspace
```

Expected: runtime files may appear as ignored (`!!`), but not as staged/tracked changes.
