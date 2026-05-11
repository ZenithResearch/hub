"""
Vault API — FastAPI HTTP service.

Access tiers:
  local  — hub/vault or same-machine path; no auth; no Bearer token needed
  remote — Matrix Bearer token required; validated against hub's Synapse

Endpoints:
  GET  /vaults                        — list configured vaults and status
  GET  /vaults/{vault_id}/contacts    — contacts with Matrix IDs
  POST /vaults/{vault_id}/init        — generate ownership keypair (first-run)

Auth is per-vault and per-request. A valid Matrix session for one vault
does not grant access to another — vault_id is part of the auth check.
"""
from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

from libs.common.logging import get_logger

from .auth import generate_keypair, public_key_exists, validate_matrix_token
from .config import VaultEntry, is_local_vault, settings
from .scanner import get_contacts

log = get_logger()


def create_app(vault_entry: VaultEntry) -> FastAPI:
    app = FastAPI(title="Hub Vault API", version="0.2.0", docs_url="/docs")
    vault_id = vault_entry.vault_id
    vault_path = vault_entry.vault_path
    local = is_local_vault(vault_path, vault_entry.vault_access)

    log.info(
        "vault_api_init",
        vault_id=vault_id,
        vault_path=vault_path,
        access_mode="local" if local else "remote (Matrix auth)",
    )

    # ── Auth helper ───────────────────────────────────────────────────────────

    async def _require_auth(authorization: str | None) -> None:
        """
        No-op for local vaults.
        For remote vaults: validates the Matrix Bearer token against Synapse.
        Raises 401 if invalid.
        """
        if local:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Matrix Bearer token required")
        token = authorization.removeprefix("Bearer ")
        user_id = await validate_matrix_token(token, settings.matrix_homeserver)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired Matrix token")
        log.info("vault_auth_ok", vault_id=vault_id, user_id=user_id)

    # ── Vault list ────────────────────────────────────────────────────────────

    @app.get("/vaults")
    def list_vaults() -> dict:
        has_key = public_key_exists(vault_path) if vault_path else False
        return {
            "vaults": [
                {
                    "vault_id": vault_id,
                    "configured": bool(vault_path),
                    "access_mode": "local" if local else "remote",
                    "auth_backend": "none" if local else "matrix",
                    "public_key_present": has_key,
                }
            ]
        }

    # ── Contacts ──────────────────────────────────────────────────────────────

    @app.get("/vaults/{vid}/contacts")
    async def contacts(
        vid: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        if vid != vault_id:
            raise HTTPException(status_code=404, detail=f"Unknown vault {vid!r}")
        await _require_auth(authorization)
        if not vault_path:
            raise HTTPException(status_code=503, detail="VAULT_PATH not configured")
        result = get_contacts(vault_path)
        return {
            "vault_id": vault_id,
            "contacts": [
                {"name": c.name, "matrix_ids": c.matrix_ids}
                for c in result
            ],
        }

    # ── Init keypair (ownership proof — PRP-PR-013) ───────────────────────────

    @app.post("/vaults/{vid}/init")
    async def init_keypair(
        vid: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """
        Generate an Ed25519 keypair for vault ownership proof.
        This is separate from access control — the keypair will be published
        to the Cardano registry (PRP-PR-013) to establish on-chain ownership.

        After calling this endpoint, move .vault/private.key to the wallet's
        secure store (Keychain or wallet service) and delete it from the vault.
        """
        if vid != vault_id:
            raise HTTPException(status_code=404, detail=f"Unknown vault {vid!r}")
        await _require_auth(authorization)
        if not vault_path:
            raise HTTPException(status_code=503, detail="VAULT_PATH not configured")
        if public_key_exists(vault_path):
            raise HTTPException(status_code=409, detail="Keypair already exists; rotate manually")
        pub = generate_keypair(vault_path)
        return {
            "vault_id": vault_id,
            "public_key": pub,
            "note": (
                "Private key written to .vault/private.key — "
                "move to wallet secure store (Keychain or wallet service) and delete from vault. "
                "Public key will be published to Cardano registry (PRP-PR-013)."
            ),
        }

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "vault_id": vault_id,
            "vault_configured": bool(vault_path),
            "access_mode": "local" if local else "remote",
        }

    return app
