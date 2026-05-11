from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class QueueSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # gRPC
    grpc_bind: str = Field(default="0.0.0.0:50053", alias="QUEUE_GRPC_BIND")

    # HTTP
    http_port: int = Field(default=8081, alias="QUEUE_HTTP_PORT")

    # Storage
    db_path: str = Field(default="/data/queue.db", alias="QUEUE_DB_PATH")

    # Claim heartbeat reaper — interval to re-queue stale claimed jobs
    reaper_interval_s: int = Field(default=30, alias="QUEUE_REAPER_INTERVAL_S")

    # Defaults applied when not specified by the caller
    default_max_retries: int = Field(default=3, alias="QUEUE_DEFAULT_MAX_RETRIES")
    default_claim_timeout_s: int = Field(
        default=300, alias="QUEUE_DEFAULT_CLAIM_TIMEOUT_S"
    )

    log_level: str = Field(default="info", alias="LOG_LEVEL")
