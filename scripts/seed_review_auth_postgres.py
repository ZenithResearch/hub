#!/usr/bin/env python3
"""Seed or rotate Review SDK auth rows in the Postgres clients registry.

This script is intentionally Postgres-only. It reads raw reviewer/deploy hook
secrets only from named environment variables, hashes them, and never prints raw
secrets or hashes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.gateway_http.review_auth import hash_access_code, hash_deploy_hook_token


def _dsn_from_env() -> str:
    explicit = (os.environ.get("CLIENTS_DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    host = os.environ.get("CLIENTS_PG_HOST", "").strip()
    password = os.environ.get("CLIENTS_PG_PASSWORD", "")
    if not host or not password:
        raise SystemExit("CLIENTS_DATABASE_URL or CLIENTS_PG_HOST/CLIENTS_PG_PASSWORD is required")
    user = quote_plus(os.environ.get("CLIENTS_PG_USER", "hub_clients"))
    password_quoted = quote_plus(password)
    database = quote_plus(os.environ.get("CLIENTS_PG_DATABASE", "hub_clients"))
    port = os.environ.get("CLIENTS_PG_PORT", "5432")
    return f"postgresql://{user}:{password_quoted}@{host}:{port}/{database}"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"required env var is missing: {name}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-slug", required=True)
    parser.add_argument("--client-name", required=True)
    parser.add_argument("--rolodex-entry-path", default="")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--deployment-slug", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--allowed-origin", required=True)
    parser.add_argument("--subject-pattern", required=True)
    parser.add_argument("--access-code-id", required=True)
    parser.add_argument("--access-label", required=True)
    parser.add_argument("--access-email", default="")
    parser.add_argument("--access-code-env", required=True)
    parser.add_argument("--deployment-scoped-access", action="store_true")
    parser.add_argument("--deploy-hook-id", default="")
    parser.add_argument("--deploy-hook-label", default="")
    parser.add_argument("--deploy-hook-token-env", default="")
    parser.add_argument("--deploy-hook-allowed-host-suffixes", default=".vercel.app")
    return parser


def main() -> int:
    ns = build_parser().parse_args()
    now = datetime.now(timezone.utc).isoformat()
    code_hash = hash_access_code(_require_env(ns.access_code_env))
    hook_hash = None
    if ns.deploy_hook_id and ns.deploy_hook_token_env:
        hook_token = _require_env(ns.deploy_hook_token_env)
        prefix = f"rdh_{ns.deploy_hook_id}_"
        if not hook_token.startswith(prefix):
            raise SystemExit("deploy hook token shape does not match deploy hook id")
        hook_hash = hash_deploy_hook_token(hook_token[len(prefix):])

    import psycopg

    with psycopg.connect(_dsn_from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO clients (id, slug, name, rolodex_entry_path, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    slug = EXCLUDED.slug,
                    name = EXCLUDED.name,
                    rolodex_entry_path = EXCLUDED.rolodex_entry_path
                """,
                (ns.client_id, ns.client_slug, ns.client_name, ns.rolodex_entry_path or None, now),
            )
            cur.execute(
                """
                INSERT INTO projects (id, client_id, slug, name, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    client_id = EXCLUDED.client_id,
                    slug = EXCLUDED.slug,
                    name = EXCLUDED.name
                """,
                (ns.project_id, ns.client_id, ns.project_slug, ns.project_name, now),
            )
            cur.execute(
                """
                INSERT INTO review_deployments
                    (id, project_id, slug, branch, allowed_origin, subject_pattern, active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    project_id = EXCLUDED.project_id,
                    slug = EXCLUDED.slug,
                    branch = EXCLUDED.branch,
                    allowed_origin = EXCLUDED.allowed_origin,
                    subject_pattern = EXCLUDED.subject_pattern,
                    active = 1,
                    updated_at = EXCLUDED.updated_at
                """,
                (ns.deployment_id, ns.project_id, ns.deployment_slug, ns.branch, ns.allowed_origin, ns.subject_pattern, now, now),
            )
            cur.execute(
                """
                INSERT INTO review_access_codes
                    (id, project_id, deployment_id, label, email, code_hash, active, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s, NULL)
                ON CONFLICT (id) DO UPDATE SET
                    project_id = EXCLUDED.project_id,
                    deployment_id = EXCLUDED.deployment_id,
                    label = EXCLUDED.label,
                    email = EXCLUDED.email,
                    code_hash = EXCLUDED.code_hash,
                    active = 1,
                    expires_at = NULL
                """,
                (
                    ns.access_code_id,
                    ns.project_id,
                    ns.deployment_id if ns.deployment_scoped_access else None,
                    ns.access_label,
                    ns.access_email or None,
                    code_hash,
                    now,
                ),
            )
            if ns.deploy_hook_id and hook_hash:
                cur.execute(
                    """
                    INSERT INTO review_deploy_hooks
                        (id, project_id, label, token_hash, allowed_host_suffixes, active, created_at, expires_at, last_used_at)
                    VALUES (%s, %s, %s, %s, %s, 1, %s, NULL, NULL)
                    ON CONFLICT (id) DO UPDATE SET
                        project_id = EXCLUDED.project_id,
                        label = EXCLUDED.label,
                        token_hash = EXCLUDED.token_hash,
                        allowed_host_suffixes = EXCLUDED.allowed_host_suffixes,
                        active = 1,
                        expires_at = NULL
                    """,
                    (
                        ns.deploy_hook_id,
                        ns.project_id,
                        ns.deploy_hook_label or f"{ns.project_name} deploy hook",
                        hook_hash,
                        ns.deploy_hook_allowed_host_suffixes,
                        now,
                    ),
                )
            cur.execute(
                """
                SELECT a.deployment_id IS NULL, a.email IS NOT NULL, a.active, d.allowed_origin, d.subject_pattern
                FROM review_access_codes a
                JOIN review_deployments d ON d.project_id = a.project_id
                WHERE a.id = %s AND d.id = %s
                """,
                (ns.access_code_id, ns.deployment_id),
            )
            row = cur.fetchone()

    print(
        json.dumps(
            {
                "ok": bool(row),
                "access_code_id": ns.access_code_id,
                "deployment_id": ns.deployment_id,
                "project_scoped_access": bool(row and row[0]),
                "email_configured": bool(row and row[1]),
                "access_active": bool(row and row[2]),
                "origin": row[3] if row else None,
                "subject_pattern": row[4] if row else None,
                "deploy_hook_seeded": bool(ns.deploy_hook_id and hook_hash),
                "secrets_printed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
