# Runtime gRPC Service

Runtime gRPC is the agent-runtime API behind Gateway. It handles message submission, event streaming, knowledge search, and tool invocation through the shared gRPC proto contract.

## Runtime entrypoint

- Compose service: `runtime-grpc`
- Source package: `services/runtime_grpc/`
- Entrypoint: `python -m services.runtime_grpc.main`
- Default local port: `50051`

## Connected services

- Gateway forwards runtime HTTP routes into this service.
- Tool Sandbox executes tool calls requested by Runtime gRPC.
- Qdrant backs knowledge/vector search.

## Main source files

- `main.py` — gRPC server setup and optional reflection.
- `service.py` — `AgentRuntimeServicer` implementation.
- `event_buffer.py` — runtime event buffering for streams.

## Focused verification

```bash
uv run pytest tests/test_model_profile_resolver.py tests/test_model_profile_check.py -q
```

Use `docker compose --profile debug run --rm grpcurl ...` for manual gRPC inspection when the stack is running.
