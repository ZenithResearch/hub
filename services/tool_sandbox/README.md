# Tool Sandbox Service

Tool Sandbox is the gRPC service that executes registered Hub tools with configured timeouts/resource defaults. It is called by Runtime gRPC and can call back to Gateway/STT for selected tool workflows.

## Runtime entrypoint

- Compose service: `tool-sandbox`
- Source package: `services/tool_sandbox/`
- Entrypoint: `python -m services.tool_sandbox.main`
- Default local port: `50052`

## Connected services

- Runtime gRPC invokes tools through this service.
- Tool definitions live under `libs/tools` by default.
- `.hermes/config-secrets.env` may be mounted read-only for local operator secrets; never commit it.

## Main source files

- `main.py` — gRPC server setup and optional reflection.
- `service.py` — `ToolSandboxServicer` implementation.

## Focused verification

```bash
uv run pytest tests/test_case_tools.py -q
```

Also run `docker compose config --quiet` after changing sandbox compose/env wiring.
