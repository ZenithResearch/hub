# Gateway HTTP

The HTTP gateway is the public-facing entry point for the hub. It runs as a FastAPI/uvicorn service on port 8080 and translates HTTP requests into gRPC calls to the runtime, with a separate file-backed intake path for review artifacts.

---

## Starting

**Full stack (recommended):**
```bash
cd ~/repos/hub
cp .env.example .env   # first time — fill in ANTHROPIC_API_KEY etc.
make up                # docker compose up --build
```

Gateway listens at `http://localhost:8080`. All services start together: `gateway-http`, `runtime-grpc`, `tool-sandbox`, `qdrant`.

**Gateway only (for review intake testing):**
```bash
cd ~/repos/hub
pip install -e ".[dev]"
uvicorn services.gateway_http.app:app --host 0.0.0.0 --port 8080 --reload
```

The review endpoints work without the gRPC runtime. The message/stream/kb/tools routes will return 502 until `runtime-grpc` is up.

---

## Routes

### Agent routes (proxied to gRPC runtime)

| Method | Path | What it does |
|--------|------|------|
| `GET` | `/health` | Health check — pings the gRPC runtime and returns its status |
| `POST` | `/v1/messages` | Submit a user message to an agent session |
| `GET` | `/v1/stream` | SSE stream of runtime events for a `request_id` |
| `POST` | `/v1/kb/search` | Vector search over the knowledge base |
| `POST` | `/v1/tools/invoke` | Invoke a registered tool directly |

These routes forward to `runtime-grpc` over gRPC. Each request gets an `x-request-id` header (generated if absent) and is logged with method, path, status, and duration.

### Review auth + intake routes (file-backed, no gRPC dependency)

| Method | Path | What it does |
|--------|------|------|
| `POST` | `/v1/review-auth/session` | Exchange a runtime reviewer access code for a short-lived review session token scoped to project, deployment, and origin. |
| `GET` | `/v1/review-auth/session` | Validate a Bearer review session token and return its project/deployment/session summary. |
| `POST` | `/v1/review-auth/deployments/register` | Server-side deploy hook endpoint. Upserts one concrete deployed review origin for a project using a DB-backed deploy hook Bearer token. |
| `POST` | `/v1/reviews/assets` | Upload an authenticated binary asset (events JSON or audio). Returns an `asset_id`. |
| `POST` | `/v1/reviews` | Submit an authenticated review record referencing uploaded asset IDs. Returns `{ review_id, status: "queued" }`. |
| `GET` | `/v1/reviews/{review_id}` | Retrieve a stored review record by ID. |

Public staging clients may expose only public configuration: Hub URL plus project/deployment identifiers. They must not bundle durable access codes, owner tokens, or review session tokens in frontend code or public environment variables.

The deploy registration flow for Vercel/CI previews is:
1. An operator creates a project-scoped deploy hook row in the Postgres clients registry. Hub stores only a hash of the raw deploy hook token plus the hook's allowed host suffix policy.
2. CI/Vercel obtains the concrete deployed origin, e.g. `https://swrl-ui-git-main-org.vercel.app`.
3. CI calls `POST /v1/review-auth/deployments/register` with `Authorization: Bearer <deploy-hook-token>`, project id/slug, deployment slug, branch, exact `allowed_origin`, matching `subject_pattern`, and optional deployment metadata.
4. Hub validates the DB-backed deploy hook token, requires HTTPS outside localhost, checks the origin host against the hook row's `allowed_host_suffixes`, ensures the subject pattern uses the same origin, and upserts `review_deployments`.
5. Reviewers then authenticate from that exact deployed origin with their client review access code. Client review access codes are separate from deploy hook tokens.

For Docker Compose, the gateway reads and writes the Review SDK clients registry through the `clients-postgres` service. If deploy registration or reviewer auth returns 401, confirm the running Postgres registry has the deploy hook, deployment, and access-code rows. Do not repair live reviewer auth by editing or copying `data/clients.db`; that SQLite path is retired for the clients registry. See `docs/local-runtime-state.md`.

Operators can seed or rotate the Postgres registry with `scripts/seed_review_auth_postgres.py`. It reads raw reviewer/deploy-hook secrets only from named environment variables, hashes them, and prints only IDs/booleans.

Example CI call:
```bash
curl -X POST "$HUB_API_URL/v1/review-auth/deployments/register" \
  -H "Authorization: Bearer $HUB_REVIEW_DEPLOY_HOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": \"swrl-ui\",
    \"deployment_slug\": \"swrl-ui-preview-${GITHUB_REF_NAME}-${GITHUB_SHA}\",
    \"branch\": \"${GITHUB_REF_NAME}\",
    \"allowed_origin\": \"https://${DEPLOY_URL#https://}\",
    \"subject_pattern\": \"https://${DEPLOY_URL#https://}/*\",
    \"commit_sha\": \"${GITHUB_SHA}\"
  }"
```

The runtime auth flow is:
1. Browser sends `POST /v1/review-auth/session` with `project_id`, `deployment_id`, optional `email`, `access_code`, and `subject_id`; the browser-controlled `Origin` header must match the deployment's allowed origin.
2. Hub verifies the access code against the Postgres clients registry and returns the raw short-lived `token` once. Hub stores only `token_hash`.
3. Browser sends `Authorization: Bearer <review-session-token>` on `POST /v1/reviews/assets` and `POST /v1/reviews`.
4. Hub validates token expiry/revocation, project/deployment scope, `Origin`, `subject_id`, and asset session ownership before writing records or enqueueing `review_submitted`.

Storage layout on disk:
```
data/reviews/
  {review_id}.json          — full attributed review record (client/project/deployment/session, subject_id, asset_ids, metadata, etc.)
  assets/
    {asset_id}              — raw binary (events JSON blob or audio/webm)
    {asset_id}.meta.json    — asset_type, mime_type, size_bytes, created_at, project/deployment/session attribution
```

Asset uploads are capped at 50 MB. All other routes use the global 256 KB body limit.

### Review status writeback

Frank Step 8 updates stored review status through Gateway using the Review Case Automaton documented in `docs/operations/review-case-automaton.md`.

Gateway keeps the public status enum stable: `queued`, `processing`, `processed`, and `failed`. Internal automaton `succeeded` is written publicly as `processed`; public `succeeded` remains invalid.

Gateway may persist additive status metadata (`automaton_status`, `automaton_event`, `review_outcome`, `review_scope`, `review_packet_path`, `review_packet_status`, and `status_reason`). It does not accept retry/rerun bookkeeping fields such as `fix_attempt_count`, `resume_step_index`, `effective_resume_parent_index`, or `rerun_step_indexes`.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HTTP_PORT` | `8080` | Port uvicorn binds to |
| `RUNTIME_GRPC_TARGET` | `runtime-grpc:50051` | Address of the gRPC runtime service |
| `CORS_ALLOW_ORIGINS` | `http://localhost:3000` | Comma-separated list of browser origins allowed to call Gateway directly. Keep local review origins such as `http://localhost:3000` and `http://localhost:5173` in operator tfvars when local Review SDK/admin clients upload assets to production Hub, and include every production browser origin such as Gallery apex/`www` aliases that upload assets directly to Hub. |
| `MAX_BODY_BYTES` | `262144` (256 KB) | Global request body size limit |
| `GATEWAY_GRPC_TIMEOUT_S` | `5.0` | Timeout for gRPC calls to the runtime |
| `REVIEWS_DATA_DIR` | `data/reviews` | Root directory for review records and assets |
| `CLIENTS_DATABASE_URL` | unset | Optional full Postgres DSN for direct/local runs; avoid using password-bearing URLs in production state/logs |
| `CLIENTS_PG_HOST` | empty | Postgres host for the Review SDK clients registry |
| `CLIENTS_PG_PORT` | `5432` | Postgres port for the Review SDK clients registry |
| `CLIENTS_PG_DATABASE` | `hub_clients` | Postgres database for the Review SDK clients registry |
| `CLIENTS_PG_USER` | `hub_clients` | Postgres user for the Review SDK clients registry |
| `CLIENTS_PG_PASSWORD` | empty | Postgres password; production injects this from Secrets Manager |
| `REVIEW_SESSION_TTL_SECONDS` | `86400` | Lifetime for short-lived review session tokens |
| `LOG_LEVEL` | `info` | Structured log level |

---

## Middleware

Two middleware layers applied to every request:

**`BodySizeLimitMiddleware`** — rejects requests that exceed `MAX_BODY_BYTES` with HTTP 413. The `/v1/reviews/assets` path gets a hardcoded 50 MB override regardless of `MAX_BODY_BYTES`.

**`RequestContextMiddleware`** — assigns a `request_id` (from the incoming `x-request-id` header or auto-generated), binds it to the structured logger for the duration of the request, and writes it back on the response. Logs each request on completion with method, path, status code, and duration.

---

## Source layout

```
services/gateway_http/
  app.py        — FastAPI app factory (create_app), all route definitions
  middleware.py — BodySizeLimitMiddleware, RequestContextMiddleware
  review_auth.py — Postgres clients registry, access-code hashing, session token validation
libs/common/
  config.py     — GatewaySettings (pydantic-settings, env-var backed)
  schemas.py    — shared request/response Pydantic models for agent routes
```
