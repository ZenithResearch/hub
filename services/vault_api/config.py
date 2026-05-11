from __future__ import annotations

import os

from pydantic_settings import BaseSettings


class VaultSettings(BaseSettings):
    vault_http_port: int = 8083
    vault_api_secret: str = "change-me"  # HMAC secret for session tokens

    # Matrix homeserver — used to validate Bearer tokens for non-local vaults
    matrix_homeserver: str = "http://localhost:8008"

    class Config:
        env_file = ".env"
        extra = "ignore"


class VaultEntry(BaseSettings):
    vault_id: str = "default"
    vault_path: str = ""

    # Access mode:
    #   local  — co-located vault (hub/vault or same-machine path); no auth required
    #   remote — any other vault; Matrix Bearer token required
    #
    # If vault_path resolves to the hub's own /app/vault directory, access mode
    # is always treated as local regardless of this setting.
    vault_access: str = "local"   # "local" | "remote"

    class Config:
        env_file = ".env"
        extra = "ignore"


# The hub's own co-located vault lives at hub/vault (or /app/vault in container).
HUB_VAULT_PATH = os.environ.get("HUB_VAULT_PATH", "/app/vault")


def is_local_vault(vault_path: str, access_mode: str) -> bool:
    """
    Returns True if the vault requires no auth.

    A vault is local when:
    - access_mode is "local" (explicit config), OR
    - vault_path resolves to the hub's own co-located vault path
    """
    if access_mode == "local":
        return True
    real = os.path.realpath(vault_path) if vault_path else ""
    return real == os.path.realpath(HUB_VAULT_PATH)


settings = VaultSettings()
vault_entry = VaultEntry()
