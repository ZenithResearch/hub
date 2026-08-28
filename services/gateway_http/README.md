# Gateway HTTP Service

Gateway HTTP is Hub's current HTTP aggregation and compatibility service. It
contains Review SDK session/asset routes, operator routes, HubFS reads, health and
OpenAPI, and proxies into Runtime, Queue, Eventbus, and Cases.

The target architecture makes Gateway private. secS-magik owns final external
admission and dispatches only verified operation context to private Hub handlers.
Current profiles still expose Gateway and let Review Auth act as an edge gate, so
they are migration substrates rather than evidence of the target boundary. See
[`../../docs/architecture/private-exposure-boundary.md`](../../docs/architecture/private-exposure-boundary.md).

## Runtime entrypoint

- Compose service: `gateway-http`
- Source package: `services/gateway_http/`
- Entrypoint: `uvicorn services.gateway_http.app:app --host 0.0.0.0 --port ${HTTP_PORT:-8080}`
- Default local port: `8080`

## Connected services

- `runtime-grpc` for agent/runtime messages, streams, KB search, and tool invocation.
- `queue` for review/case work intake.
- `eventbus` for wakeups.
- `cases` for admin case inspection routes.
- `clients-postgres` for Review SDK client/project/deployment/access-code/session state.
- Local `.hermes` and `/data` mounts for operator config, review assets, and HubFS roots.

## Main source files

- `app.py` — FastAPI app factory and route definitions.
- `middleware.py` — body-size and request-context middleware.
- `review_auth.py` — legacy/current Review workflow registry, access-code hashing,
  deploy hooks, and session tokens; not the target external admission authority.
- `static/dashboard.html` — lightweight dashboard/static surface.

## Current docs

- `../../docs/gateway-http.md` — route map and Review SDK auth/session details.
- `../../docs/local-runtime-state.md` — local runtime-state and privacy boundary.
- `../../docs/operations/production-rollout.md` — production rollout path.

## Focused verification

```bash
uv run pytest tests/test_gateway_http_sessions.py tests/test_review_auth_postgres_backend.py -q
uv run pytest tests/test_image_env_manifest_check.py -q
```
