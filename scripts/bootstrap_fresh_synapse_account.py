#!/usr/bin/env python3
"""Bootstrap the isolated Synapse Terraform authority with verified AWS account root."""

import argparse
import configparser
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile, mkstemp
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

EXPECTED_PROFILE = "zenith-hypha-free"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_ROOT_ARN = "arn:aws:iam::610992396917:root"
EXPECTED_BUCKET = "hypha-synapse-terraform-state-610992396917-us-east-1"
EXPECTED_ROLE_ARN = "arn:aws:iam::610992396917:role/HyphaSynapseDeploymentRole"
EXPECTED_SOURCE_USER = "HyphaSynapseTerraformSource"
SOURCE_PROFILE = "zenith-hypha-bootstrap"
DEPLOYMENT_PROFILE = "zenith-hypha-synapse"
STACK_NAME = "hypha-synapse-bootstrap"
EXPECTED_TOPIC_ARN = "arn:aws:sns:us-east-1:610992396917:hypha-synapse-expiry-alerts"
SAFE_OUTPUT_KEYS = frozenset(
    ("DeploymentRoleArn", "DeploymentSourceUserName", "ExpiryAlertTopicArn", "StateBucketName")
)


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


def _run_aws_profile(profile: str, arguments: Sequence[str]) -> str:
    command = [
        "aws",
        "--no-cli-pager",
        "--profile",
        profile,
        "--region",
        EXPECTED_REGION,
        *arguments,
    ]
    environment = _aws_environment()
    environment["AWS_PROFILE"] = profile
    process = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    if process.returncode != 0:
        raise BootstrapError("AWS CLI profile verification failed")
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


def stack_exists() -> bool:
    process = subprocess.run(
        [
            "aws",
            "--no-cli-pager",
            "--profile",
            EXPECTED_PROFILE,
            "--region",
            EXPECTED_REGION,
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            STACK_NAME,
            "--output",
            "json",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_aws_environment(),
    )
    return process.returncode == 0


def deploy(template: Path, alert_email: Optional[str]) -> None:
    if not template.is_file():
        raise BootstrapError("bootstrap template is missing")
    parameter_path: Optional[str] = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", prefix="hypha-synapse-bootstrap-", delete=False) as handle:
            parameter_path = handle.name
            os.chmod(parameter_path, 0o600)
            parameter = (
                "AlertEmail=" + alert_email
                if alert_email is not None
                else "ParameterKey=AlertEmail,UsePreviousValue=true"
            )
            json.dump([parameter], handle)
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
        "DeploymentSourceUserName": EXPECTED_SOURCE_USER,
        "ExpiryAlertTopicArn": EXPECTED_TOPIC_ARN,
        "StateBucketName": EXPECTED_BUCKET,
    }:
        raise BootstrapError("stack returned unexpected output values")
    return outputs


def _write_ini(path: Path, config: configparser.RawConfigParser) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_path = mkstemp(prefix=path.name + "-", dir=str(path.parent))
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            config.write(handle)
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def _configure_local_profiles() -> None:
    credentials_path = Path.home() / ".aws" / "credentials"
    credentials = configparser.RawConfigParser()
    if credentials_path.exists():
        credentials.read(credentials_path)

    response = _parse_json_object(
        _run_aws(("iam", "list-access-keys", "--user-name", EXPECTED_SOURCE_USER, "--output", "json")),
        "deployment source access keys",
    )
    keys = response.get("AccessKeyMetadata")
    if not isinstance(keys, list) or len(keys) > 1:
        raise BootstrapError("deployment source must have at most one access key")

    stored_key_id = credentials.get(SOURCE_PROFILE, "aws_access_key_id", fallback=None)
    stored_secret = credentials.get(SOURCE_PROFILE, "aws_secret_access_key", fallback=None)
    if keys:
        live_key_id = keys[0].get("AccessKeyId") if isinstance(keys[0], dict) else None
        if not live_key_id or stored_key_id != live_key_id or not stored_secret:
            raise BootstrapError("deployment source key exists but is not installed in the exact local profile")
    else:
        created = _parse_json_object(
            _run_aws(("iam", "create-access-key", "--user-name", EXPECTED_SOURCE_USER, "--output", "json")),
            "deployment source access key",
        ).get("AccessKey")
        if not isinstance(created, dict):
            raise BootstrapError("AWS returned an invalid deployment source access key")
        stored_key_id = created.get("AccessKeyId")
        stored_secret = created.get("SecretAccessKey")
        if not isinstance(stored_key_id, str) or not isinstance(stored_secret, str):
            raise BootstrapError("AWS returned an invalid deployment source access key")
        if not credentials.has_section(SOURCE_PROFILE):
            credentials.add_section(SOURCE_PROFILE)
        credentials.set(SOURCE_PROFILE, "aws_access_key_id", stored_key_id)
        credentials.set(SOURCE_PROFILE, "aws_secret_access_key", stored_secret)
        _write_ini(credentials_path, credentials)

    config_path = Path.home() / ".aws" / "config"
    config = configparser.RawConfigParser()
    if config_path.exists():
        config.read(config_path)
    for section in ("profile " + SOURCE_PROFILE, "profile " + DEPLOYMENT_PROFILE):
        if not config.has_section(section):
            config.add_section(section)
        config.set(section, "region", EXPECTED_REGION)
        config.set(section, "output", "json")
    deployment_section = "profile " + DEPLOYMENT_PROFILE
    config.set(deployment_section, "source_profile", SOURCE_PROFILE)
    config.set(deployment_section, "role_arn", EXPECTED_ROLE_ARN)
    _write_ini(config_path, config)

    source_identity = None
    for delay in (0, 2, 5, 10, 20):
        if delay:
            time.sleep(delay)
        try:
            source_identity = _parse_json_object(
                _run_aws_profile(SOURCE_PROFILE, ("sts", "get-caller-identity", "--output", "json")),
                "deployment source identity",
            )
            break
        except BootstrapError:
            continue
    if source_identity is None or source_identity.get("Arn") != (
        "arn:aws:iam::" + EXPECTED_ACCOUNT + ":user/" + EXPECTED_SOURCE_USER
    ):
        raise BootstrapError("deployment source profile identity could not be verified")
    deployment_identity = _parse_json_object(
        _run_aws_profile(DEPLOYMENT_PROFILE, ("sts", "get-caller-identity", "--output", "json")),
        "deployment role identity",
    )
    deployment_arn = deployment_identity.get("Arn")
    if (
        deployment_identity.get("Account") != EXPECTED_ACCOUNT
        or not isinstance(deployment_arn, str)
        or ":assumed-role/HyphaSynapseDeploymentRole/" not in deployment_arn
    ):
        raise BootstrapError("bounded deployment role could not be verified")


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
    alert_email = None
    if not stack_exists():
        alert_email = getpass.getpass("Primary AWS account alert email: ").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", alert_email):
            print("refusing bootstrap: a valid alert email is required", file=sys.stderr)
            return 2
    try:
        deploy(template, alert_email)
        outputs = safe_stack_outputs()
        _configure_local_profiles()
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    outputs["DeploymentProfile"] = DEPLOYMENT_PROFILE
    print(json.dumps(outputs, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
