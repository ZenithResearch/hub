---
title: Inbox
type: moc
description: "Hub intake queue — persistent job store with gRPC + HTTP API; source co-located here so the knowledge graph links directly to implementation"
---

# Inbox

The hub's workspace queue. Events enter as jobs; any worker can claim and process them. Producer-agnostic, consumer-agnostic — the queue has no knowledge of routing, agents, or event sources.

**Status: implemented.** Source lives in this directory — vault notes can link directly to implementation files.

## Source

- [__init__.py](__init__.py)
- [models.py](models.py) — `Job`, `QueueInfo`, `JobStatus`
- [store.py](store.py) — SQLite-backed queue store; WAL mode; reaper for stale jobs
- [service.py](service.py) — gRPC `QueueService` implementation
- [http.py](http.py) — FastAPI HTTP interface (`/queues`, `/jobs`)
- [main.py](main.py) — entry point; starts gRPC (port 50053) + HTTP (port 8081) concurrently
- [config.py](config.py) — `QueueSettings` (env vars)

## Proto

- [proto/queue.proto](../proto/queue.proto) — `QueueService` protobuf definition
- [libs/common/proto/queue_pb2.py](../libs/common/proto/queue_pb2.py) — generated stubs

## Operations

| Operation | gRPC | HTTP |
|---|---|---|
| Enqueue | `QueueService/Enqueue` | `POST /queues/{name}/enqueue` |
| Dequeue (claim) | `QueueService/Dequeue` | `POST /queues/{name}/dequeue?worker_id=X` |
| Ack | `QueueService/Ack` | `POST /jobs/{id}/ack` |
| Nack (retry/DLQ) | `QueueService/Nack` | `POST /jobs/{id}/nack` |
| Get job | `QueueService/GetJob` | `GET /jobs/{id}` |
| List queues | `QueueService/ListQueues` | `GET /queues` |
| Peek | `QueueService/Peek` | `GET /queues/{name}/peek?n=10` |
| Health | `QueueService/HealthCheck` | `GET /health` |

## Job lifecycle

```
pending → processing → done
                     ↘ failed (on nack, retries remaining)
                     ↘ dlq    (on nack, retries exhausted or force_dlq=true)
                     ↗ pending (reaper reclaims stale claimed jobs after claim_timeout_s)
```

## Architecture notes

- SQLite with WAL mode — single-writer, concurrent-reader; no external dependency
- Reaper thread reclaims jobs where `claimed_at + claim_timeout_s < now()`
- Priority: higher integer = processed first within a queue
- DLQ is a status on the same `jobs` table, not a separate queue — queryable via `Peek(status="dlq")`
- Upgrade path: swap `store.py` for a Postgres-backed implementation when throughput requires it

## Related

- [[the queue is a standalone durable job store — it has no opinion about who produces or consumes and no knowledge of routing logic]]
- [[capture directories serve as typed event inboxes in the vault-native event-driven architecture]]
- [outbox/](../outbox/index.md) — mirrors this with identical retry semantics for outbound events
