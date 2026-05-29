# Gateway HTTP Service

Gateway HTTP is the public/API edge for Hub. It owns browser-facing Review SDK auth and asset routes, admin/operator routes, HubFS reads, public health/openapi, and proxy routes into runtime, queue, eventbus, and cases.

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
- `review_auth.py` — clients registry, access-code hashing, deploy hooks, session tokens.
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
