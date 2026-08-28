# Zenith Hub

Zenith Hub is a local-first private runtime substrate. Its intentionally small
product surface is DevGraph, Matrix, durable Queue, private inference, object
storage, private deployment, and operability. Cases, Frank, Runtime, workers,
tools, review processing, and Gateway are internal implementation components.

This README is an orientation map. It should help you find the right subsystem before you open the deeper service and operations docs.

## Status: active WIP

This repository is under heavy development.

Expect breaking changes in:

- service names and ports
- API routes
- process contracts
- Frank/Hermes worker behavior
- config shape
- production rollout conventions

Do not treat this repo as a stable SDK or production platform yet. The target
architecture is virtually private and exposes operations only after secS-magik
admission. That boundary is not implemented today: current Gateway and Matrix
profiles still contain direct public paths. See
[`docs/architecture/private-exposure-boundary.md`](docs/architecture/private-exposure-boundary.md)
for the contract and gap inventory. Treat the docs as layered: root README for
navigation, service READMEs for implementation details, operations docs for
deploy/runbooks, and `docs/plans/` for historical planning context.

## Current center of gravity

The most reliable parts of the current system are the orchestration backbone:

- queue intake and leases (`inbox/`)
- event wakeups (`services/eventbus/`)
- durable case state (`services/cases/`)
- Frank native case execution (`services/frank/`)
- Matrix local messaging infrastructure (`infra/matrix/`, `services/ingest/`, `services/matrix_bridge/`)

If you are trying to understand the working system, start with those pieces. Treat dashboard/static UI surfaces, review-packet internals, ZenithOS operator APIs, and historical plans as active development surfaces until you verify them against current code.

## Architecture sketch

```text
external caller / agent / app
             |
             v
  secS-magik verifier + permissioned RPC
             |
             | verified operation context only
             v
       private Hub receiver
             |
             +--> DevGraph
             +--> Matrix
             +--> Queue
             +--> inference server
             +--> object storage

  Gateway, Cases, Frank, Runtime, tools, workers, databases, and admin APIs
  remain private implementation components behind this boundary.
```

secS-magik owns the final external allow/deny decision. Hub owns private handlers,
domain behavior, and state mutations after admission. Wallets, browser login, and
other identity systems may provide evidence to secS; Hub does not accept them as a
parallel authorization path.

## Repository map

| Path | Responsibility | Start here |
|---|---|---|
| `services/` | Runtime service packages: Gateway, Cases, Frank, Eventbus, STT, workers, indexers, Matrix bridges, and gRPC services. | `services/README.md` |
| `inbox/` | Queue service package used by the `queue` compose/ECS service. | `inbox/README.md` |
| `libs/` | Shared config, schemas, proto helpers, tool contracts, case tools, and reusable library code. | `libs/common/`, `libs/tools/` |
| `base/ops/` | Process and skill definitions used by Hub and Frank. | `base/ops/processes/` |
| `rolodex/` | Agent identities, configs, and worker skill surfaces. | `rolodex/agents/frank/` |
| `infra/` | Infrastructure docs/config for local Matrix and cloud/on-prem profiles. | `infra/README.md` |
| `infra/aws_baseline_80/` | AWS production Terraform baseline and deployment docs. | `infra/aws_baseline_80/DEPLOYMENT.md` |
| `scripts/` | Local startup, setup, build, rollout, smoke, and seeding helpers. | `scripts/start.sh`, `scripts/prod_build_image.sh`, `scripts/prod_terraform_cd.sh` |
| `tests/` | Regression, contract, source-truth, and deployment guard tests. | `tests/test_gateway_http_sessions.py`, `tests/test_cases_contract.py` |
| `docs/` | Current architecture/operations docs plus historical plans. | `docs/README.md` |
| `repos/workspace/` | Gitignored local client/project repos the Hub may inspect or operate on. | See “Workspace repos” below. |

## Service map

Every code service has a local README. Use this table to jump from runtime concept to source package.

| Runtime service / concept | Source package | Protocol / port | README |
|---|---|---:|---|
| Gateway HTTP | `services/gateway_http/` | HTTP `8080` | `services/gateway_http/README.md` |
| Queue | `inbox/` | HTTP `8081`, gRPC `50053` | `inbox/README.md` |
| Eventbus | `services/eventbus/` | HTTP `8082` | `services/eventbus/README.md` |
| Cases | `services/cases/` | HTTP `8083` | `services/cases/README.md` |
| Frank | `services/frank/` | queue/event driven | `services/frank/README.md` |
| STT HTTP | `services/stt_http/` | HTTP `8765` | `services/stt_http/README.md` |
| Runtime gRPC | `services/runtime_grpc/` | gRPC `50051` | `services/runtime_grpc/README.md` |
| Tool sandbox | `services/tool_sandbox/` | gRPC `50052` | `services/tool_sandbox/README.md` |
| Hermes worker queue | `services/hermes_worker_queue/` | queue/event driven | `services/hermes_worker_queue/README.md` |
| Matrix bridge | `services/matrix_bridge/` | HTTP `8084` | `services/matrix_bridge/README.md` |
| Matrix ingest | `services/ingest/` | Matrix client loop | `services/ingest/README.md` |
| Feeds | `services/feeds/` | polling worker | `services/feeds/README.md` |
| KB indexer | `services/kb_indexer/` | one-shot worker | `services/kb_indexer/README.md` |
| Process indexer | `services/process_indexer/` | one-shot worker | `services/process_indexer/README.md` |
| Vault API | `services/vault_api/` | HTTP service | `services/vault_api/README.md` |
| Vault indexer | `services/vault_indexer/` | indexer library/worker | `services/vault_indexer/README.md` |

Supporting compose dependencies are documented in `services/README.md`: `clients-postgres`, `qdrant`, `matrix-synapse`, and `matrix-db` are third-party backing services rather than Hub-owned service packages.

## Local startup

The normal local startup path is:

```bash
cp .env.example .env
./scripts/start.sh
```

Expect to edit `.env` for local secrets, model providers, Matrix tokens, STT provider settings, and service-specific settings.

Runtime state is intentionally local-only. The repo can be pushed publicly while real usage data stays ignored: `.env`, `.hermes/`, `data/*.db`, review artifacts, generated Matrix registrations, and `repos/workspace/` are not tracked. See `docs/local-runtime-state.md`.

For focused development, prefer targeted compose/test commands over assuming the whole stack is stable:

```bash
docker compose up --build gateway-http queue eventbus cases frank stt-http
uv run pytest tests/test_gateway_http_sessions.py tests/test_cases_contract.py -q
```

## Review audio and speech-to-text (STT)

Frank transcribes review audio through `services/frank/stt_client.py`. The current production baseline is managed ElevenLabs Scribe v2 with local Whisper fallback; local development defaults to local Whisper through the `stt-http` service.

| Setting | Local default | Production baseline | Notes |
|---|---|---|---|
| `STT_PROVIDER` | `local_whisper` | `elevenlabs` | Primary provider. Options: `local_whisper`, `elevenlabs`. |
| `STT_MODEL` | `tiny` | `scribe_v2` | Whisper options include `tiny`, `base`, `small`, `medium`, `large`, `large-v2`, `large-v3`, `turbo`; ElevenLabs uses `scribe_v2`. |
| `STT_FALLBACK_PROVIDER` | `none` | `local_whisper` | Fallback provider after primary failure. |
| `STT_AUDIO_PREPROCESSOR` | `none` | `none` | Optional: `elevenlabs_audio_isolation`; keep off until sample comparisons prove it helps. |
| `STT_HTTP_URL` | `http://stt-http:8765` | internal Cloud Map URL | Used by local Whisper fallback. |
| `ELEVENLABS_API_KEY` | empty | AWS Secrets Manager injection | Required for ElevenLabs STT or audio isolation; never commit real values. |

Useful docs:

- `services/stt_http/README.md` — local Whisper HTTP service.
- `services/frank/README.md` — Frank STT provider boundary and native pipeline.
- `docs/ops/elevenlabs-stt-rollout.md` — STT options, secret posture, smoke tests, rollback.

## Matrix-backed local community

Matrix is the chat protocol. Synapse is the Matrix homeserver used by this repo.

The fastest local community/groupchat path is:

```bash
cp .env.example .env
./scripts/start.sh
```

On first run, `scripts/start.sh` starts Synapse, runs `scripts/setup_matrix_bots.sh`, generates appservice tokens, recreates Synapse with generated appservice registrations, creates a `feedback` Matrix room, invites `bridge-bot`, and writes `MATRIX_FEEDBACK_ROOM_ID` back into `.env`.

Start with:

- `infra/matrix/README.md` — Synapse/appservice runtime details.
- `services/ingest/README.md` — Matrix → queue intake.
- `services/matrix_bridge/README.md` — Hub → Matrix bridge callbacks.

Current caveat: Matrix/Synapse can host multiple rooms today, but Hub bridge/orchestration routing should still be treated as WIP. `feedback` is the room wired by the default setup path; additional rooms may need explicit bridge routing/config before they become first-class Hub channels.

## Production updates

A pushed commit is not automatically a live Hub deploy. The standard production path is clean source first, then one full Terraform plan/apply:

1. land the desired source on clean `main` and push it;
2. wait for CI to pass;
3. build and push immutable image tags from clean `main` with `scripts/prod_build_image.sh` or equivalent manifest copy where appropriate;
4. inspect live ECS image tags for all services;
5. run a reviewed Terraform plan with explicit service tags;
6. apply the saved Terraform plan;
7. wait for ECS stability and run smokes;
8. optionally confirm a no-op Terraform plan with the same convergence vars.

Start here:

- `docs/operations/production-rollout.md` — standard clean-source image build + Terraform rollout.
- `docs/operations/operator-updates.md` — operator-state doctrine and update planner boundaries.
- `docs/operations/production-source-ledger.md` — source/image/live-state provenance ledger from the main-head convergence audit.
- `infra/aws_baseline_80/DEPLOYMENT.md` — AWS production inventory and smoke details.

## Workspace repos

`repos/workspace/` is gitignored. Put local client/project repos there when the Hub needs to inspect or operate on them without committing those repos into Hub.

```bash
cd repos/workspace
ln -s ~/path/to/project ./project-name
# or
git clone https://github.com/example/project-name
```

## Contributing / issues

This is not yet a polished open-source project. If something breaks, seems confusing, or you want to coordinate before building on top of it, DM:

- X/Twitter: `@bananawalnutz`

Please include:

- what you tried
- what commit you were on
- relevant service logs or case IDs
- whether the issue is queue/cases/Synapse related or an experimental surface

## License

License pending / to be finalized before broader release.

## secS-magik exposure target

secS-magik is the intended sole logical external admission gate for every Hub operation.
The Hub repository does **not** yet contain an embedded or co-deployed secS
integration, verified-call context decoder, receiver manifest, or end-to-end tests. Current
Gateway, Matrix, AWS, and on-premises exposure paths therefore do not conform.

secS is a required logical layer, not necessarily a separate external service.
Hub may import a pinned secS implementation into its private receiver or deploy a
pinned secS service/sidecar with Hub. The packaging decision remains open; both
modes must reject unauthorized calls before any Hub side effect.

The target preserves ownership boundaries:

- secS verifies evidence, operation authority, audience, expiry, and replay state;
- Hub receives only verified context on a private interface and owns the resulting
  operation and domain state;
- identity or wallet systems may supply evidence to secS but do not authorize Hub
  directly.

No global opcode is allocated here and no `ZenithPacket` v0 contract is changed.
See the [private exposure boundary](docs/architecture/private-exposure-boundary.md)
and [seven controlled claims](docs/architecture/capability-claims.md).
