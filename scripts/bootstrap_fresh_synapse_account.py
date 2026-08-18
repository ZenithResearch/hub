#!/usr/bin/env python3
"""Bootstrap the isolated Synapse Terraform authority with verified AWS account root."""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Mapping, Optional, Sequence

EXPECTED_PROFILE = "zenith-hypha-free"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_ROOT_ARN = "arn:aws:iam::610992396917:root"
EXPECTED_BUCKET = "hypha-synapse-terraform-state-610992396917-us-east-1"
EXPECTED_ROLE_ARN = "arn:aws:iam::610992396917:role/HyphaSynapseDeploymentRole"
STACK_NAME = "hypha-synapse-bootstrap"
EXPECTED_TOPIC_ARN = "arn:aws:sns:us-east-1:610992396917:hypha-synapse-expiry-alerts"
SAFE_OUTPUT_KEYS = frozenset(("DeploymentRoleArn", "ExpiryAlertTopicArn", "StateBucketName"))


class BootstrapError(RuntimeError):
    """A fail-closed bootstrap error containing no secret material."""


def _aws_environment() -> Dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_STS",
        "AWS_ENDPOINT_URL_CLOUDFORMATION",
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
        raise BootstrapError("AWS CLI execution failed") from exc
    if process.returncode != 0:
        raise BootstrapError("AWS CLI command failed")
    return process.stdout


def _parse_json_object(raw: str, description: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BootstrapError("AWS CLI returned invalid JSON for " + description) from exc
    if not isinstance(value, dict):
        raise BootstrapError("AWS CLI returned invalid JSON for " + description)
    return value


def verify_root_identity() -> None:
    identity = _parse_json_object(
        _run_aws(("sts", "get-caller-identity", "--output", "json")),
        "caller identity",
    )
    if identity.get("Account") != EXPECTED_ACCOUNT or identity.get("Arn") != EXPECTED_ROOT_ARN:
        raise BootstrapError("refusing bootstrap: caller is not the exact target account root")


def deploy(template: Path, alert_email: str) -> None:
    if not template.is_file():
        raise BootstrapError("bootstrap template is missing")
    parameter_path: Optional[str] = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", prefix="hypha-synapse-bootstrap-", delete=False) as handle:
            parameter_path = handle.name
            os.chmod(parameter_path, 0o600)
            json.dump(["AlertEmail=" + alert_email], handle)
            handle.write("\n")
        _run_aws(
            (
                "cloudformation",
                "deploy",
                "--stack-name",
                STACK_NAME,
                "--template-file",
                str(template),
                "--capabilities",
                "CAPABILITY_NAMED_IAM",
                "--parameter-overrides",
                "file://" + parameter_path,
                "--no-fail-on-empty-changeset",
            )
        )
    finally:
        if parameter_path is not None:
            try:
                os.unlink(parameter_path)
            except FileNotFoundError:
                pass


def safe_stack_outputs() -> Dict[str, str]:
    response = _parse_json_object(
        _run_aws(
            (
                "cloudformation",
                "describe-stacks",
                "--stack-name",
                STACK_NAME,
                "--output",
                "json",
            )
        ),
        "stack outputs",
    )
    stacks = response.get("Stacks")
    if not isinstance(stacks, list) or len(stacks) != 1 or not isinstance(stacks[0], dict):
        raise BootstrapError("AWS CLI returned an invalid stack response")
    raw_outputs = stacks[0].get("Outputs")
    if not isinstance(raw_outputs, list):
        raise BootstrapError("AWS CLI returned invalid stack outputs")

    outputs: Dict[str, str] = {}
    for item in raw_outputs:
        if not isinstance(item, dict):
            raise BootstrapError("AWS CLI returned invalid stack outputs")
        key = item.get("OutputKey")
        value = item.get("OutputValue")
        if key in SAFE_OUTPUT_KEYS:
            if not isinstance(value, str) or not value:
                raise BootstrapError("AWS CLI returned invalid stack outputs")
            outputs[key] = value
    if set(outputs) != SAFE_OUTPUT_KEYS:
        raise BootstrapError("stack did not return all expected safe outputs")
    if outputs != {
        "DeploymentRoleArn": EXPECTED_ROLE_ARN,
        "ExpiryAlertTopicArn": EXPECTED_TOPIC_ARN,
        "StateBucketName": EXPECTED_BUCKET,
    }:
        raise BootstrapError("stack returned unexpected output values")
    return outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing bootstrap: exact profile and region are required", file=sys.stderr)
        return 2

    try:
        verify_root_identity()
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    template = Path(__file__).resolve().parents[1] / "infra" / "matrix" / "aws" / "bootstrap.yaml"
    alert_email = getpass.getpass("Primary AWS account alert email: ").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", alert_email):
        print("refusing bootstrap: a valid alert email is required", file=sys.stderr)
        return 2
    try:
        deploy(template, alert_email)
        outputs = safe_stack_outputs()
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(outputs, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
