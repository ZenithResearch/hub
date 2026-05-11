from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Matrix homeserver
    matrix_homeserver: str = Field(default="http://matrix-synapse:8008", alias="MATRIX_HOMESERVER")
    matrix_user: str = Field(default="@sophia:localhost", alias="SOPHIA_MATRIX_USER")

    # App service tokens — no password; Sophia is registered as an application service
    as_token: str = Field(default="", alias="SOPHIA_AS_TOKEN")   # ingest → Synapse
    hs_token: str = Field(default="", alias="SOPHIA_HS_TOKEN")   # Synapse → ingest
    as_port: int  = Field(default=8083, alias="SOPHIA_AS_PORT")  # port we listen on

    # Queue
    queue_http_url: str = Field(default="http://queue:8081", alias="QUEUE_HTTP_URL")
    queue_name: str = Field(default="workspace", alias="QUEUE_NAME")

    # Event bus
    eventbus_url: str = Field(default="http://eventbus:8082", alias="EVENTBUS_URL")

    # Which room IDs are "concierge" channels (1:1, session-managed)
    # Comma-separated list in env: "!abc:localhost,!def:localhost"
    concierge_room_ids_raw: str = Field(default="", alias="CONCIERGE_ROOM_IDS")

    @property
    def concierge_room_ids(self) -> list[str]:
        return [r.strip() for r in self.concierge_room_ids_raw.split(",") if r.strip()]

    log_level: str = Field(default="info", alias="LOG_LEVEL")
