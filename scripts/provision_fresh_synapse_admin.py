#!/usr/bin/env python3
"""Provision the sole fresh Synapse administrator without exposing credentials."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

from provision_matrix_admins import (
    ProvisioningError,
    http_json,
    provision_admins,
    store_in_keychain,
)

EXPECTED_PROFILE = "zenith-hypha-synapse"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_SECRET_NAME = "hypha/fresh-synapse/runtime"
EXPECTED_ENDPOINT = "https://synapse.zenith-research.ca"
EXPECTED_USERNAME = "beaver"
EXPECTED_USER_ID = "@beaver:synapse.zenith-research.ca"
EXPECTED_ROLE_ARN_FRAGMENT = "assumed-role/HyphaSynapseDeploymentRole/"
REQUIRED_SECRET_KEYS = {
    "POSTGRES_PASSWORD",
    "REGISTRATION_SHARED_SECRET",
    "MACAROON_SECRET_KEY",
    "FORM_SECRET",
}


class FreshAdminError(RuntimeError):
    """A fail-closed error containing no credential material."""


def _aws_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_STS",
        "AWS_ENDPOINT_URL_SECRETS_MANAGER",
    ):
        environment.pop(name, None)
    environment["AWS_PROFILE"] = EXPECTED_PROFILE
    environment["AWS_REGION"] = EXPECTED_REGION
    environment["AWS_DEFAULT_REGION"] = EXPECTED_REGION
    return environment


def _run_aws(arguments: Sequence[str]) -> str:
    command = [
        "aws",
        "--no-cli-pager",
        "--profile",
        EXPECTED_PROFILE,
        "--region",
        EXPECTED_REGION,
        *arguments,
    ]
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_aws_environment(),
        )
    except OSError as exc:
        raise FreshAdminError("AWS CLI execution failed") from exc
    if process.returncode != 0:
        raise FreshAdminError("AWS CLI command failed")
    return process.stdout


def _json_object(raw: str, description: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise FreshAdminError("AWS returned invalid JSON for " + description) from exc
    if not isinstance(value, dict):
        raise FreshAdminError("AWS returned invalid JSON for " + description)
    return value


def _verify_role() -> None:
    identity = _json_object(
        _run_aws(("sts", "get-caller-identity", "--output", "json")),
        "caller identity",
    )
    arn = identity.get("Arn")
    if identity.get("Account") != EXPECTED_ACCOUNT or not isinstance(arn, str):
        raise FreshAdminError("refusing administrator provisioning: wrong AWS account")
    if EXPECTED_ROLE_ARN_FRAGMENT not in arn:
        raise FreshAdminError("refusing administrator provisioning: deployment role is required")


def _registration_secret() -> str:
    response = _json_object(
        _run_aws(
            (
                "secretsmanager",
                "get-secret-value",
                "--secret-id",
                EXPECTED_SECRET_NAME,
                "--version-stage",
                "AWSCURRENT",
                "--output",
                "json",
            )
        ),
        "runtime secret",
    )
    if response.get("Name") != EXPECTED_SECRET_NAME:
        raise FreshAdminError("runtime secret identity mismatch")
    raw = response.get("SecretString")
    if not isinstance(raw, str):
        raise FreshAdminError("runtime secret value is unavailable")
    values = _json_object(raw, "runtime secret value")
    if set(values) != REQUIRED_SECRET_KEYS:
        raise FreshAdminError("runtime secret key set mismatch")
    registration_secret = values.get("REGISTRATION_SHARED_SECRET")
    if not isinstance(registration_secret, str) or len(registration_secret) < 32:
        raise FreshAdminError("registration authority is invalid")
    return registration_secret


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing administrator provisioning: exact profile and region are required", file=sys.stderr)
        return 2
    try:
        _verify_role()
        results = provision_admins(
            [EXPECTED_USERNAME],
            secret_loader=_registration_secret,
            http=http_json,
            keychain_store=store_in_keychain,
            endpoint=EXPECTED_ENDPOINT,
        )
        if len(results) != 1 or results[0].get("user_id") != EXPECTED_USER_ID:
            raise FreshAdminError("administrator provisioning returned an unexpected identity")
    except (FreshAdminError, ProvisioningError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
