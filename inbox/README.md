# Queue Service (`inbox/`)

The runtime service named `queue` is implemented in this package. It owns durable work intake, claiming, retry, timeout/reaper behavior, and HTTP/gRPC interfaces used by Gateway, Frank, Matrix ingest, feeds, and Hermes workers.

## Runtime entrypoint

- Compose service: `queue`
- Source package: `inbox/`
- Entrypoint: `python -m inbox.main`
- Default HTTP port: `8081`
- Default gRPC port: `50053`
- Local DB path: `${QUEUE_DB_PATH:-/data/queue.db}`

## Connected services

- Gateway enqueues review and admin work.
- Frank and Hermes Worker Queue claim and settle work.
- Eventbus wakes consumers after new work arrives.
- Matrix ingest and Feeds can enqueue normalized external attention items.

## Main source files

- `main.py` — process entrypoint wiring HTTP/gRPC queue surfaces.
- `http.py` — HTTP queue API.
- `service.py` — queue service behavior.
- `store.py` — SQLite-backed durable store.
- `models.py` — queue item models.
- `config.py` — env-backed settings.
- `types/` — queue item type definitions/templates.

## Focused verification

```bash
uv run pytest tests/test_queue_http.py tests/test_eventbus_broker.py -q
```
