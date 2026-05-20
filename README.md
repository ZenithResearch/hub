# Zenith Hub

Zenith Hub is a local-first orchestration runtime for Zenith experiments: queues, durable case state, Matrix/Synapse messaging, Frank dispatch, Hermes worker profiles, review processing, and operator-facing APIs used by ZenithOS.

## Status: active WIP

This repository is under heavy development.

Expect many breaking changes:

- service names and ports may change
- API routes may change
- process contracts may change
- Frank/Hermes worker behavior may change
- config shape may change
- docs may lag behind the code
- old plans and architecture notes may describe paths that have already been replaced

Do not treat this repo as a stable SDK or production platform yet.

## What you can mostly trust right now

The most reliable parts of the current system are the orchestration backbone:

- queue services
- event wakeups
- cases service and durable case state
- case/step/slot/log progression
- Matrix local messaging infrastructure, currently backed by Synapse

If you are trying to understand the working system, start from those pieces. Treat them as the current center of gravity.

## What to treat as unstable

Everything else should be considered experimental unless you have just verified it locally:

- Frank runtime internals
- Hermes worker profile launch behavior
- review-packet processing surfaces
- ZenithOS operator APIs
- dashboard/static UI surfaces
- worker skills and process definitions
- historical docs under `docs/plans/`

Some of these work well today, but they are still active development surfaces and may change without migration support.

## Current architecture sketch

```text
Gateway / UI / Matrix
        |
        v
      Queue  <---- event wakeups
        |
        v
      Frank  ---- creates / updates ----> Cases
        |                                  |
        |                                  v
        |                            durable state
        |                         cases / steps / slots
        v
 Hermes workers / tools / review processing
```

Matrix is the local messaging layer, currently backed by Synapse. Queue and cases are the orchestration source of truth. Frank and workers should be understood as execution/control surfaces around that durable core.

## Local startup

The normal local startup path is:

```bash
cp .env.example .env
./scripts/start.sh
```

Expect to edit `.env` for local secrets, model providers, Matrix tokens, and service-specific settings.

Runtime state is intentionally local-only. The repo can be pushed publicly while real usage data stays ignored: `.env`, `.hermes/`, `data/*.db`, review artifacts, generated Matrix registrations, and `repos/workspace/` are not tracked. See `docs/local-runtime-state.md`.

For focused development, prefer targeted compose/test commands over assuming the whole stack is stable.

## Quick Matrix-backed community

Matrix is the chat protocol. Synapse is the Matrix homeserver used by this repo.

The fastest local community/groupchat path is:

```bash
cp .env.example .env
./scripts/start.sh
```

On first run, `scripts/start.sh` starts Synapse, runs `scripts/setup_matrix_bots.sh`, generates appservice tokens, recreates Synapse with the generated appservice registrations, creates a `feedback` Matrix room, invites `bridge-bot`, and writes `MATRIX_FEEDBACK_ROOM_ID` back into `.env`.

After startup:

```bash
source .env
curl http://localhost:${MATRIX_HTTP_PORT:-8008}/health
printf 'feedback room: %s\n' "$MATRIX_FEEDBACK_ROOM_ID"
```

To create a small multi-room community on the same local Synapse instance, create more Matrix rooms with the gateway appservice user and invite the bridge bot:

```bash
source .env
DOMAIN=${MATRIX_SERVER_NAME:-localhost}
HOMESERVER=${MATRIX_HOMESERVER_URL:-http://localhost:8008}

for ROOM_NAME in general support announcements; do
  ROOM_ID=$(curl -sf -X POST \
    "$HOMESERVER/_matrix/client/v3/createRoom?user_id=$MATRIX_GATEWAY_BOT_USER_ID" \
    -H "Authorization: Bearer $GATEWAY_BOT_AS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$ROOM_NAME\",\"preset\":\"private_chat\",\"invite\":[\"@bridge-bot:$DOMAIN\"]}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['room_id'])")

  curl -sf -X POST \
    "$HOMESERVER/_matrix/client/v3/join/$ROOM_ID?user_id=@bridge-bot:$DOMAIN" \
    -H "Authorization: Bearer $BRIDGE_BOT_AS_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{}" > /dev/null

  printf '%s %s\n' "$ROOM_NAME" "$ROOM_ID"
done
```

Current caveat: Matrix/Synapse can host multiple rooms today, but Hub's bridge/orchestration routing should still be treated as WIP. `feedback` is the room wired by the default setup path; additional rooms may need explicit bridge routing/config before they become first-class Hub channels.

### Sophia Matrix token setup

If a Matrix client for Sophia shows `HTTP 401: Invalid access token passed.`, the Sophia appservice token is not registered with the local Synapse instance currently serving `@sophia:<server>`.

Each fork/local checkout must generate its own Matrix tokens. Do not copy tokens from another clone or from committed history.

To enable Sophia's local Matrix appservice:

```bash
./scripts/setup_matrix_sophia.sh
```

To rotate Sophia's appservice tokens and re-register them with Synapse:

```bash
./scripts/setup_matrix_sophia.sh --rotate
```

That script updates `.env`, sets `MATRIX_REGISTER_SOPHIA_APP_SERVICE=true`, recreates Synapse so it renders `sophia.resolved.yaml` inside the runtime `/data/appservices` volume, starts the `ingest` receiver, and verifies `@sophia:<server>` with `/account/whoami`. Generated `.resolved.yaml` files are runtime artifacts and must not be committed.

## Repository layout

```text
hub/
├── services/        runtime services: gateway, queue, cases, Frank, workers, STT, eventbus
├── libs/            shared config, tools, contracts, and case tool implementations
├── base/ops/        process and skill definitions used by the hub
├── rolodex/         agent identities, configs, and worker skill surfaces
├── infra/           Matrix/Synapse and other infrastructure config
├── scripts/         local startup, setup, and acceptance helpers
├── tests/           regression and contract tests
├── docs/            current docs plus historical plans/reports
└── repos/           connected repo registry and local-only workspace
```

## Workspace repos

`repos/workspace/` is gitignored. Put local client/project repos there when the hub needs to inspect or operate on them without committing those repos into Hub.

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
