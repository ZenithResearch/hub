# Hermes Worker Queue Service

Hermes Worker Queue is the service bridge between Hub queue items and Hermes agent execution. It claims worker-queue tasks, launches/coordinates Hermes work, forwards case state, and settles queue entries.

## Runtime entrypoint

- Compose service: `hermes-worker-queue`
- Source package: `services/hermes_worker_queue/`
- Entrypoint: `python -m services.hermes_worker_queue.main`

## Connected services

- `queue` for work claims and settlement.
- `eventbus` for wakeups.
- `cases` for state reconciliation.
- `stt-http` and `gateway-http` for review/case workflows.
- Host Docker socket may be mounted for worker sandbox execution in local compose.

## Main source files

- `main.py` — worker queue loop, dispatch configuration, settlement/retry behavior.

## Focused verification

```bash
uv run pytest tests/test_hermes_worker_queue.py -q
```
