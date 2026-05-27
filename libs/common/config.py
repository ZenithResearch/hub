from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: Literal["debug", "info", "warning", "error", "critical"] = Field(
        default="info", alias="LOG_LEVEL"
    )


class GatewaySettings(BaseServiceSettings):
    http_port: int = Field(default=8080, alias="HTTP_PORT")
    runtime_grpc_target: str = Field(
        default="runtime-grpc:50051", alias="RUNTIME_GRPC_TARGET"
    )
    cors_allow_origins: str = Field(
        default="http://localhost:3000", alias="CORS_ALLOW_ORIGINS"
    )
    max_body_bytes: int = Field(default=262_144, alias="MAX_BODY_BYTES")

    reviews_data_dir: str = Field(default="data/reviews", alias="REVIEWS_DATA_DIR")
    clients_db_backend: Literal["sqlite", "postgres"] = Field(default="sqlite", alias="CLIENTS_DB_BACKEND")
    clients_db_path: str = Field(default="data/clients.db", alias="CLIENTS_DB_PATH")
    clients_database_url: str = Field(default="", alias="CLIENTS_DATABASE_URL")
    clients_pg_host: str = Field(default="", alias="CLIENTS_PG_HOST")
    clients_pg_port: int = Field(default=5432, alias="CLIENTS_PG_PORT")
    clients_pg_database: str = Field(default="hub_clients", alias="CLIENTS_PG_DATABASE")
    clients_pg_user: str = Field(default="hub_clients", alias="CLIENTS_PG_USER")
    clients_pg_password: str = Field(default="", alias="CLIENTS_PG_PASSWORD")
    review_session_ttl_seconds: int = Field(default=86_400, alias="REVIEW_SESSION_TTL_SECONDS")
    review_access_admin_token: str = Field(default="", alias="REVIEW_ACCESS_ADMIN_TOKEN")
    hermes_session_roots: str = Field(default="", alias="HERMES_SESSION_ROOTS")
    hub_config_secrets_path: str = Field(default="/data/hub-config-secrets.env", alias="HUB_CONFIG_SECRETS_PATH")
    model_profiles_path: str = Field(default="infra/model-profiles.yaml", alias="MODEL_PROFILES_PATH")
    model_profile_overrides_path: str = Field(default="/data/model-profile-overrides.yaml", alias="MODEL_PROFILE_OVERRIDES_PATH")
    model_profile_audit_path: str = Field(default="/data/model-profile-audit.jsonl", alias="MODEL_PROFILE_AUDIT_PATH")
    queue_http_url: str = Field(default="http://localhost:8081", alias="QUEUE_HTTP_URL")
    eventbus_url: str = Field(default="http://localhost:8082", alias="EVENTBUS_URL")
    cases_http_url: str = Field(default="http://cases:8083", alias="CASES_HTTP_URL")
    hubfs_allowed_roots: str = Field(default="/data", alias="HUBFS_ALLOWED_ROOTS")

    # Matrix inbox — gateway posts review submissions to the feedback room as gateway-bot
    matrix_homeserver_url: str = Field(default="", alias="MATRIX_HOMESERVER_URL")
    matrix_bot_user_id: str = Field(default="", alias="MATRIX_GATEWAY_BOT_USER_ID")
    # as_token — gateway sends this to Synapse to authenticate as gateway-bot
    matrix_bot_access_token: str = Field(default="", alias="GATEWAY_BOT_AS_TOKEN")
    matrix_feedback_room_id: str = Field(default="", alias="MATRIX_FEEDBACK_ROOM_ID")

    # Outbound timeouts
    grpc_timeout_s: float = Field(default=5.0, alias="GATEWAY_GRPC_TIMEOUT_S")


class RuntimeSettings(BaseServiceSettings):
    bind_addr: str = Field(default="0.0.0.0:50051", alias="RUNTIME_GRPC_BIND")
    tool_sandbox_grpc_target: str = Field(
        default="tool-sandbox:50052", alias="TOOL_SANDBOX_GRPC_TARGET"
    )
    qdrant_url: str = Field(default="http://qdrant:6333", alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")

    tool_dir: str = Field(default="/app/libs/tools", alias="TOOL_DIR")
    tool_default_timeout_ms: int = Field(default=5000, alias="TOOL_DEFAULT_TIMEOUT_MS")
    tool_default_max_memory_mb: int = Field(
        default=128, alias="TOOL_DEFAULT_MAX_MEMORY_MB"
    )

    kb_vector_dim: int = Field(default=256, alias="KB_VECTOR_DIM")
    qdrant_collection: str = Field(default="kb_documents", alias="QDRANT_COLLECTION")

    grpc_client_timeout_s: float = Field(default=5.0, alias="RUNTIME_GRPC_CLIENT_TIMEOUT_S")


class ToolSandboxSettings(BaseServiceSettings):
    bind_addr: str = Field(default="0.0.0.0:50052", alias="TOOL_SANDBOX_GRPC_BIND")
    tool_dir: str = Field(default="/app/libs/tools", alias="TOOL_DIR")
    tool_default_timeout_ms: int = Field(default=5000, alias="TOOL_DEFAULT_TIMEOUT_MS")
    tool_default_max_memory_mb: int = Field(
        default=128, alias="TOOL_DEFAULT_MAX_MEMORY_MB"
    )
    allow_tools_with_network: bool = Field(
        default=True, alias="ALLOW_TOOLS_WITH_NETWORK"
    )

    stdout_max_bytes: int = Field(default=65_536, alias="TOOL_STDOUT_MAX_BYTES")
    stderr_max_bytes: int = Field(default=65_536, alias="TOOL_STDERR_MAX_BYTES")


class VaultSettings(BaseServiceSettings):
    vault_path: str | None = Field(
        default=None,
        alias="VAULT_PATH",
        description="Absolute path to the co-located vault root. Required for hub→vault writes.",
    )


class MatrixBridgeSettings(BaseServiceSettings):
    matrix_homeserver_url: str = Field(default="", alias="MATRIX_HOMESERVER_URL")
    # hs_token — Synapse sends this to authenticate transaction pushes to the bridge
    matrix_bot_access_token: str = Field(default="", alias="BRIDGE_BOT_HS_TOKEN")
    matrix_feedback_room_id: str = Field(default="", alias="MATRIX_FEEDBACK_ROOM_ID")
    queue_http_url: str = Field(default="http://queue:8081", alias="QUEUE_HTTP_URL")
    eventbus_url: str = Field(default="http://eventbus:8082", alias="EVENTBUS_URL")


class QueueSettings(BaseServiceSettings):
    grpc_bind: str = Field(default="0.0.0.0:50053", alias="QUEUE_GRPC_BIND")
    http_port: int = Field(default=8081, alias="QUEUE_HTTP_PORT")
    db_path: str = Field(default="/data/queue.db", alias="QUEUE_DB_PATH")
    reaper_interval_s: int = Field(default=30, alias="QUEUE_REAPER_INTERVAL_S")
    default_max_retries: int = Field(default=3, alias="QUEUE_DEFAULT_MAX_RETRIES")
    default_claim_timeout_s: int = Field(default=300, alias="QUEUE_DEFAULT_CLAIM_TIMEOUT_S")


class IndexerSettings(BaseServiceSettings):
    qdrant_url: str = Field(default="http://qdrant:6333", alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")
    kb_base_dir: str = Field(default="/app/base", alias="KB_BASE_DIR")
    ops_dir: str = Field(default="/app/ops", alias="OPS_DIR")

    kb_vector_dim: int = Field(default=256, alias="KB_VECTOR_DIM")
    qdrant_collection: str = Field(default="kb_documents", alias="QDRANT_COLLECTION")

    qdrant_startup_timeout_s: float = Field(
        default=30.0, alias="QDRANT_STARTUP_TIMEOUT_S"
    )
