# Eventbus Service

Eventbus is the lightweight HTTP wakeup/broadcast service. It lets producers publish events and lets Hub workers/services poll or subscribe for work notifications without treating the queue itself as a pub/sub system.

## Runtime entrypoint

- Compose service: `eventbus`
- Source package: `services/eventbus/`
- Entrypoint: `python -m services.eventbus.main`
- Default local port: `8082`

## Connected services

- Gateway publishes review/case/admin events.
- Queue-adjacent workers use event wakeups to avoid blind polling.
- Frank and Hermes worker queue respond to events and then inspect Queue/Cases for authoritative state.
- Matrix bridge, ingest, and feeds use eventbus for integration wakeups.

## Main source files

- `main.py` — uvicorn entrypoint.
- `http.py` — FastAPI routes.
- `broker.py` — in-process event broker logic.

## Focused verification

```bash
uv run pytest tests/test_eventbus_broker.py -q
```
