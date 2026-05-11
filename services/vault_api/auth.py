"""
Vault auth.

Access tiers:
  local  — hub/vault or same-machine path; no auth required
  remote — any other vault; Matrix Bearer token required

For remote vaults, the client passes their Matrix access token as a Bearer token.
The vault API validates it against the hub's Synapse homeserver via:
    GET /_matrix/client/v3/account/whoami

This reuses existing Matrix auth — no separate credential system needed.

Ownership proof (Ed25519 keypair, PRP-PR-013) is a separate concern from access
control and is not enforced here. The keypair establishes who owns the vault on-chain;
Matrix establishes who is accessing it in-session.

rclone mounts:
  Remote storage backends (S3, Dropbox, SFTP, etc.) are mounted by rclone at
  /vaults/{vault_id}/ before the vault API reads from them. The auth layer runs
  before any filesystem access — if auth fails, the mount is never read.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import aiohttp

from libs.common.logging import get_logger

log = get_logger()


# ── Matrix auth ────────────────────────────────────────────────────────────────

async def validate_matrix_token(token: str, matrix_homeserver: str) -> str | None:
    """
    Validates a Matrix access token against the hub's Synapse instance.
    Returns the Matrix user_id on success, None on failure.
    """
    url = f"{matrix_homeserver}/_matrix/client/v3/account/whoami"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("user_id")
                log.warning("vault_matrix_auth_failed", status=resp.status)
                return None
    except Exception as e:
        log.warning("vault_matrix_auth_error", error=str(e))
        return None


# ── Keypair (ownership proof only — not enforced for access control) ───────────

PUBLIC_KEY_RELATIVE = ".vault/public.key"


def public_key_exists(vault_path: str) -> bool:
    return (Path(vault_path) / PUBLIC_KEY_RELATIVE).exists()


def generate_keypair(vault_path: str) -> str:
    """
    Generate an Ed25519 keypair for a vault and write both keys to .vault/.
    Public key: {vault_path}/.vault/public.key  (base64url)
    Private key: {vault_path}/.vault/private.key (base64url)

    The private key should be immediately moved to the wallet's secure store
    (Keychain on macOS, or wallet service) and deleted from the vault filesystem.
    The public key stays in the vault and will be published to the Cardano registry
    (PRP-PR-013) to establish verifiable ownership.

    Returns the public key as a base64url string.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    pub_bytes = public_key.public_bytes_raw()
    priv_bytes = private_key.private_bytes_raw()

    vault_dir = Path(vault_path) / ".vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")
    priv_b64 = base64.urlsafe_b64encode(priv_bytes).decode().rstrip("=")

    (vault_dir / "public.key").write_text(pub_b64 + "\n")
    (vault_dir / "private.key").write_text(priv_b64 + "\n")

    log.info("vault_keypair_generated", vault_path=vault_path)
    return pub_b64
