#!/usr/bin/env python3
"""Populate the fresh Synapse runtime secret without exposing secret values."""

import argparse
import json
import os
import secrets
import subprocess
import sys
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Mapping, Optional, Sequence

EXPECTED_PROFILE = "zenith-hypha-synapse"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_SECRET_NAME = "hypha/fresh-synapse/runtime"
EXPECTED_ROLE_ARN_FRAGMENT = "assumed-role/HyphaSynapseDeploymentRole/"
REQUIRED_KEYS = (
    "POSTGRES_PASSWORD",
    "REGISTRATION_SHARED_SECRET",
    "MACAROON_SECRET_KEY",
    "FORM_SECRET",
)


class PopulationError(RuntimeError):
    """A fail-closed error that contains no secret material."""


def _aws_environment() -> Dict[str, str]:
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
    ] + list(arguments)
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
        raise PopulationError("AWS CLI execution failed") from exc
    if process.returncode != 0:
        raise PopulationError("AWS CLI command failed")
    return process.stdout


def _json_object(raw: str, description: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PopulationError("AWS returned invalid JSON for " + description) from exc
    if not isinstance(value, dict):
        raise PopulationError("AWS returned invalid JSON for " + description)
    return value


def _verify_role() -> None:
    identity = _json_object(
        _run_aws(("sts", "get-caller-identity", "--output", "json")),
        "caller identity",
    )
    arn = identity.get("Arn")
    if identity.get("Account") != EXPECTED_ACCOUNT or not isinstance(arn, str):
        raise PopulationError("refusing secret population: wrong AWS account")
    if EXPECTED_ROLE_ARN_FRAGMENT not in arn:
        raise PopulationError("refusing secret population: deployment role is required")


def _verify_secret() -> str:
    secret = _json_object(
        _run_aws(("secretsmanager", "describe-secret", "--secret-id", EXPECTED_SECRET_NAME, "--output", "json")),
        "secret metadata",
    )
    arn = secret.get("ARN")
    if secret.get("Name") != EXPECTED_SECRET_NAME or not isinstance(arn, str):
        raise PopulationError("runtime secret identity mismatch")
    expected_prefix = "arn:aws:secretsmanager:us-east-1:610992396917:secret:hypha/fresh-synapse/runtime-"
    if not arn.startswith(expected_prefix):
        raise PopulationError("runtime secret ARN mismatch")
    if secret.get("DeletedDate") is not None:
        raise PopulationError("runtime secret is pending deletion")
    return arn


def _fresh_values() -> Dict[str, str]:
    return {key: secrets.token_urlsafe(48) for key in REQUIRED_KEYS}


def _populate(secret_arn: str) -> Mapping[str, Any]:
    path: Optional[str] = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", prefix="hypha-synapse-secret-", delete=False) as handle:
            path = handle.name
            os.chmod(path, 0o600)
            json.dump(_fresh_values(), handle, separators=(",", ":"), sort_keys=True)
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
        raise PopulationError("secret version response identity mismatch")
    stages = response.get("VersionStages")
    if stages != ["AWSCURRENT"]:
        raise PopulationError("secret version did not become the sole requested stage")
    return {
        "Name": EXPECTED_SECRET_NAME,
        "VersionId": response.get("VersionId"),
        "VersionStages": stages,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing secret population: exact profile and region are required", file=sys.stderr)
        return 2
    try:
        _verify_role()
        secret_arn = _verify_secret()
        safe_metadata = _populate(secret_arn)
    except PopulationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(safe_metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
