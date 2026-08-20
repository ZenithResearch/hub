#!/usr/bin/env python3
"""Add or rotate broker credentials in the existing Synapse runtime secret."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import subprocess
import sys
from tempfile import NamedTemporaryFile
from typing import Any, Mapping, Sequence

from services.hypha_admin_broker.auth import encode_scrypt_verifier

EXPECTED_PROFILE = "zenith-hypha-synapse"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_SECRET_NAME = "hypha/fresh-synapse/runtime"
EXPECTED_ROLE_ARN_FRAGMENT = "assumed-role/HyphaSynapseDeploymentRole/"
BASE_KEYS = {
    "POSTGRES_PASSWORD",
    "REGISTRATION_SHARED_SECRET",
    "MACAROON_SECRET_KEY",
    "FORM_SECRET",
}
BROKER_KEYS = {
    "HYPHA_ADMIN_BROKER_SECRET_VERIFIER",
    "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD",
}


class BrokerSecretRotationError(RuntimeError):
    """A fail-closed rotation error that contains no secret material."""


def _environment() -> dict[str, str]:
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
    try:
        process = subprocess.run(
            [
                "aws",
                "--no-cli-pager",
                "--profile",
                EXPECTED_PROFILE,
                "--region",
                EXPECTED_REGION,
                *arguments,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_environment(),
        )
    except OSError as exc:
        raise BrokerSecretRotationError("AWS CLI execution failed") from exc
    if process.returncode != 0:
        raise BrokerSecretRotationError("AWS CLI command failed")
    return process.stdout


def _json_object(raw: str, description: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrokerSecretRotationError("AWS returned invalid " + description) from exc
    if not isinstance(value, dict):
        raise BrokerSecretRotationError("AWS returned invalid " + description)
    return value


def _verify_role_and_secret() -> str:
    identity = _json_object(
        _run_aws(("sts", "get-caller-identity", "--output", "json")),
        "caller identity",
    )
    arn = identity.get("Arn")
    if (
        identity.get("Account") != EXPECTED_ACCOUNT
        or not isinstance(arn, str)
        or EXPECTED_ROLE_ARN_FRAGMENT not in arn
    ):
        raise BrokerSecretRotationError("bounded deployment identity was not established")
    metadata = _json_object(
        _run_aws(
            (
                "secretsmanager",
                "describe-secret",
                "--secret-id",
                EXPECTED_SECRET_NAME,
                "--output",
                "json",
            )
        ),
        "secret metadata",
    )
    secret_arn = metadata.get("ARN")
    prefix = "arn:aws:secretsmanager:us-east-1:610992396917:secret:hypha/fresh-synapse/runtime-"
    if (
        metadata.get("Name") != EXPECTED_SECRET_NAME
        or not isinstance(secret_arn, str)
        or not secret_arn.startswith(prefix)
        or metadata.get("DeletedDate") is not None
    ):
        raise BrokerSecretRotationError("runtime secret identity mismatch")
    return secret_arn


def _load_current(secret_arn: str) -> dict[str, str]:
    raw = _run_aws(
        (
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_arn,
            "--version-stage",
            "AWSCURRENT",
            "--query",
            "SecretString",
            "--output",
            "text",
        )
    )
    value = _json_object(raw, "runtime secret value")
    if frozenset(value) not in {frozenset(BASE_KEYS), frozenset(BASE_KEYS | BROKER_KEYS)}:
        raise BrokerSecretRotationError("runtime secret schema is not eligible for broker rotation")
    if any(not isinstance(item, str) or not item for item in value.values()):
        raise BrokerSecretRotationError("runtime secret schema is not eligible for broker rotation")
    return {key: item for key, item in value.items() if isinstance(item, str)}


def rotated_values(current: Mapping[str, str], operator_secret: str) -> dict[str, str]:
    if frozenset(current) not in {frozenset(BASE_KEYS), frozenset(BASE_KEYS | BROKER_KEYS)}:
        raise BrokerSecretRotationError("runtime secret schema is not eligible for broker rotation")
    try:
        verifier = encode_scrypt_verifier(operator_secret)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise BrokerSecretRotationError("administration secret did not satisfy the required policy") from exc
    updated = dict(current)
    updated["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"] = verifier
    updated.setdefault("HYPHA_ADMIN_BROKER_SERVICE_PASSWORD", secrets.token_urlsafe(48))
    return updated


def _read_operator_secret() -> str:
    first = getpass.getpass("New Hypha administration secret: ")
    confirmed = getpass.getpass("Confirm Hypha administration secret: ")
    if first != confirmed:
        raise BrokerSecretRotationError("administration secret confirmation did not match")
    return first


def _put(secret_arn: str, values: Mapping[str, str]) -> dict[str, Any]:
    path: str | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="hypha-admin-broker-secret-",
            delete=False,
        ) as handle:
            path = handle.name
            os.chmod(path, 0o600)
            json.dump(values, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
        response = _json_object(
            _run_aws(
                (
                    "secretsmanager",
                    "put-secret-value",
                    "--secret-id",
                    secret_arn,
                    "--version-stages",
                    "AWSCURRENT",
                    "--secret-string",
                    "file://" + path,
                    "--output",
                    "json",
                )
            ),
            "secret version metadata",
        )
    finally:
        if path is not None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    if response.get("ARN") != secret_arn or response.get("Name") != EXPECTED_SECRET_NAME:
        raise BrokerSecretRotationError("secret version response identity mismatch")
    if response.get("VersionStages") != ["AWSCURRENT"]:
        raise BrokerSecretRotationError("secret version did not become the requested stage")
    return {
        "Name": EXPECTED_SECRET_NAME,
        "VersionId": response.get("VersionId"),
        "VersionStages": response.get("VersionStages"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing rotation: exact profile and region are required", file=sys.stderr)
        return 2
    try:
        secret_arn = _verify_role_and_secret()
        current = _load_current(secret_arn)
        updated = rotated_values(current, _read_operator_secret())
        safe_metadata = _put(secret_arn, updated)
    except BrokerSecretRotationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(safe_metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
