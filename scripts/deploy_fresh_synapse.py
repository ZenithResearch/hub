#!/usr/bin/env python3
"""Launch the fresh standalone Synapse stack from configuration and print its DNS record."""

from __future__ import annotations

import argparse
import configparser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any, Sequence

EXPECTED_PROFILE = "zenith-hypha-free"
EXPECTED_DEPLOYMENT_PROFILE = "zenith-hypha-synapse"
EXPECTED_SOURCE_PROFILE = "zenith-hypha-bootstrap"
EXPECTED_ROLE_SESSION_NAME = "hypha-synapse-deploy"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_ROLE_ARN = "arn:aws:iam::610992396917:role/HyphaSynapseDeploymentRole"
SECRET_NAME = "hypha/fresh-synapse/runtime"
BASE_CREATES = {
    "aws_iam_instance_profile.matrix",
    "aws_iam_role.matrix",
    "aws_iam_role_policy.matrix_secret",
    "aws_iam_role_policy_attachment.ssm",
    "aws_internet_gateway.matrix",
    "aws_route_table.matrix",
    "aws_route_table_association.matrix",
    "aws_secretsmanager_secret.matrix",
    "aws_security_group.matrix",
    "aws_subnet.matrix",
    "aws_vpc.matrix",
}
RUNTIME_CREATES = {"aws_instance.matrix[0]", "aws_eip.matrix[0]"}


class DeploymentError(RuntimeError):
    """Fail-closed deployment error containing no secret values."""


def _environment(profile: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_PROFILE",
    ):
        environment.pop(name, None)
    environment["AWS_PROFILE"] = profile
    environment["AWS_REGION"] = EXPECTED_REGION
    environment["AWS_DEFAULT_REGION"] = EXPECTED_REGION
    environment["TF_IN_AUTOMATION"] = "1"
    return environment


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    profile: str | None = None,
    capture: bool = False,
) -> str:
    process = subprocess.run(
        list(command),
        cwd=cwd,
        env=_environment(profile) if profile else None,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if process.returncode != 0:
        raise DeploymentError("deployment command failed")
    return process.stdout if capture else ""


def _write_configuration(aws_dir: Path, hostname: str, ami_id: str, instance_type: str, volume_size: int, runtime: bool) -> None:
    backend = (
        'bucket       = "hypha-synapse-terraform-state-610992396917-us-east-1"\n'
        'key          = "fresh-synapse/prod/terraform.tfstate"\n'
        'region       = "us-east-1"\n'
        "encrypt      = true\n"
        "use_lockfile = true\n"
        f'profile      = "{EXPECTED_SOURCE_PROFILE}"\n'
        "assume_role = {\n"
        f'  role_arn = "{EXPECTED_ROLE_ARN}"\n'
        "}\n"
    )
    variables = (
        f'aws_region         = "{EXPECTED_REGION}"\n'
        f'ami_id             = "{ami_id}"\n'
        f'matrix_server_name = "{hostname}"\n\n'
        f'instance_type       = "{instance_type}"\n'
        f"data_volume_size_gb = {volume_size}\n"
        f"enable_runtime      = {'true' if runtime else 'false'}\n"
    )
    (aws_dir / "backend.hcl").write_text(backend, encoding="utf-8")
    (aws_dir / "terraform.tfvars").write_text(variables, encoding="utf-8")
    os.chmod(aws_dir / "backend.hcl", 0o600)
    os.chmod(aws_dir / "terraform.tfvars", 0o600)


def _changed_actions(plan: dict[str, Any]) -> dict[str, list[str]]:
    changes: dict[str, list[str]] = {}
    for resource in plan.get("resource_changes", []):
        if not isinstance(resource, dict):
            raise DeploymentError("Terraform plan contains an invalid resource change")
        address = resource.get("address")
        actions = resource.get("change", {}).get("actions")
        if not isinstance(address, str) or not isinstance(actions, list):
            raise DeploymentError("Terraform plan contains an invalid resource change")
        if actions != ["no-op"]:
            changes[address] = actions
    return changes


def validate_plan(plan: dict[str, Any], allowed_creates: set[str]) -> dict[str, list[str]]:
    changes = _changed_actions(plan)
    unexpected = set(changes) - allowed_creates
    if unexpected or any(actions != ["create"] for actions in changes.values()):
        raise DeploymentError("Terraform plan contains an unapproved action")
    return changes


def needs_base_stage(state: Sequence[str]) -> bool:
    return not any(address in state for address in RUNTIME_CREATES)


def deployment_profiles_installed(home: Path | None = None) -> bool:
    root = home or Path.home()
    config = configparser.RawConfigParser()
    credentials = configparser.RawConfigParser()
    config.read(root / ".aws" / "config")
    credentials.read(root / ".aws" / "credentials")
    section = "profile " + EXPECTED_DEPLOYMENT_PROFILE
    return (
        config.get(section, "role_arn", fallback=None) == EXPECTED_ROLE_ARN
        and config.get(section, "source_profile", fallback=None) == EXPECTED_SOURCE_PROFILE
        and config.get(section, "role_session_name", fallback=None) == EXPECTED_ROLE_SESSION_NAME
        and config.get(section, "region", fallback=None) == EXPECTED_REGION
        and credentials.has_section(EXPECTED_SOURCE_PROFILE)
        and bool(credentials.get(EXPECTED_SOURCE_PROFILE, "aws_access_key_id", fallback=None))
        and bool(credentials.get(EXPECTED_SOURCE_PROFILE, "aws_secret_access_key", fallback=None))
    )


def runtime_verification_commands() -> tuple[str, ...]:
    return (
        "set -euo pipefail",
        "cloud-init status --wait",
        'test "$(cloud-init status)" = "status: done"',
        'test "$(findmnt -n -o FSTYPE /opt/matrix-data)" = "xfs"',
        'test "$(findmnt -n -o LABEL /opt/matrix-data)" = "hypha-matrix"',
        'test "$(systemctl is-active docker)" = "active"',
        (
            "for attempt in $(seq 1 120); do "
            "db_health=$(docker inspect --format='{{.State.Health.Status}}' matrix-db 2>/dev/null || true); "
            "synapse_health=$(docker inspect --format='{{.State.Health.Status}}' matrix-synapse 2>/dev/null || true); "
            "if [ \"$db_health\" = healthy ] && [ \"$synapse_health\" = healthy ] && "
            "docker exec matrix-synapse python -c \"import urllib.request; "
            "r=urllib.request.urlopen('http://127.0.0.1:8008/_matrix/client/versions', timeout=10); "
            "assert r.status == 200\" >/dev/null 2>&1; then exit 0; fi; "
            "sleep 5; done; echo 'runtime did not become healthy' >&2; exit 1"
        ),
    )


def _verify_runtime(root: Path, instance_id: str) -> None:
    online_deadline = time.monotonic() + 600
    while time.monotonic() < online_deadline:
        raw = _run(
            (
                "aws",
                "ssm",
                "describe-instance-information",
                "--filters",
                "Key=InstanceIds,Values=" + instance_id,
                "--output",
                "json",
            ),
            cwd=root,
            profile=EXPECTED_DEPLOYMENT_PROFILE,
            capture=True,
        )
        try:
            rows = json.loads(raw).get("InstanceInformationList", [])
        except (AttributeError, json.JSONDecodeError) as exc:
            raise DeploymentError("AWS returned invalid managed-instance metadata") from exc
        if rows and isinstance(rows[0], dict) and rows[0].get("PingStatus") == "Online":
            break
        time.sleep(5)
    else:
        raise DeploymentError("runtime did not become available through SSM")

    with TemporaryDirectory(prefix="hypha-synapse-verify-") as temporary_name:
        parameter_path = Path(temporary_name) / "commands.json"
        parameter_path.write_text(
            json.dumps({"commands": list(runtime_verification_commands())}),
            encoding="utf-8",
        )
        os.chmod(parameter_path, 0o600)
        raw = _run(
            (
                "aws",
                "ssm",
                "send-command",
                "--instance-ids",
                instance_id,
                "--document-name",
                "AWS-RunShellScript",
                "--comment",
                "Verify fresh Synapse runtime",
                "--parameters",
                "file://" + str(parameter_path),
                "--output",
                "json",
            ),
            cwd=root,
            profile=EXPECTED_DEPLOYMENT_PROFILE,
            capture=True,
        )
    try:
        command_id = json.loads(raw)["Command"]["CommandId"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise DeploymentError("AWS returned invalid runtime verification metadata") from exc
    if not isinstance(command_id, str) or not command_id:
        raise DeploymentError("AWS returned invalid runtime verification metadata")

    command_deadline = time.monotonic() + 900
    while time.monotonic() < command_deadline:
        try:
            raw = _run(
                (
                    "aws",
                    "ssm",
                    "list-commands",
                    "--command-id",
                    command_id,
                    "--output",
                    "json",
                ),
                cwd=root,
                profile=EXPECTED_DEPLOYMENT_PROFILE,
                capture=True,
            )
        except DeploymentError:
            time.sleep(5)
            continue
        try:
            commands = json.loads(raw).get("Commands", [])
        except (AttributeError, json.JSONDecodeError) as exc:
            raise DeploymentError("AWS returned invalid runtime verification status") from exc
        status = commands[0].get("Status") if commands and isinstance(commands[0], dict) else None
        if status == "Success":
            return
        if status in {"Cancelled", "Cancelling", "Failed", "TimedOut"}:
            raise DeploymentError("runtime verification failed")
        time.sleep(5)
    raise DeploymentError("runtime verification timed out")


def _plan_and_apply(aws_dir: Path, temporary: Path, name: str, allowed_creates: set[str]) -> None:
    plan_path = temporary / (name + ".tfplan")
    json_path = temporary / (name + ".json")
    _run(
        ("terraform", "plan", "-input=false", "-out=" + str(plan_path)),
        cwd=aws_dir,
        profile=EXPECTED_DEPLOYMENT_PROFILE,
    )
    rendered = _run(
        ("terraform", "show", "-json", str(plan_path)),
        cwd=aws_dir,
        profile=EXPECTED_DEPLOYMENT_PROFILE,
        capture=True,
    )
    json_path.write_text(rendered, encoding="utf-8")
    try:
        plan = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise DeploymentError("Terraform returned invalid plan JSON") from exc
    changes = validate_plan(plan, allowed_creates)
    if changes:
        _run(
            ("terraform", "apply", "-input=false", str(plan_path)),
            cwd=aws_dir,
            profile=EXPECTED_DEPLOYMENT_PROFILE,
        )


def _secret_has_current(root: Path) -> bool:
    raw = _run(
        (
            "aws",
            "secretsmanager",
            "describe-secret",
            "--secret-id",
            SECRET_NAME,
            "--output",
            "json",
        ),
        cwd=root,
        profile=EXPECTED_DEPLOYMENT_PROFILE,
        capture=True,
    )
    try:
        secret = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeploymentError("AWS returned invalid secret metadata") from exc
    mapping = secret.get("VersionIdsToStages")
    return isinstance(mapping, dict) and any(
        isinstance(stages, list) and "AWSCURRENT" in stages for stages in mapping.values()
    )


def _safe_outputs(aws_dir: Path) -> dict[str, str]:
    raw = _run(
        ("terraform", "output", "-json"),
        cwd=aws_dir,
        profile=EXPECTED_DEPLOYMENT_PROFILE,
        capture=True,
    )
    try:
        outputs = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeploymentError("Terraform returned invalid outputs") from exc
    safe: dict[str, str] = {}
    for name in ("elastic_ip", "instance_id", "matrix_url"):
        item = outputs.get(name)
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(value, str) or not value:
            raise DeploymentError("Terraform did not return all safe runtime outputs")
        safe[name] = value
    return safe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--ami-id", required=True)
    parser.add_argument("--instance-type", default="t3.small")
    parser.add_argument("--data-volume-size-gb", type=int, default=30)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing deployment: exact bootstrap profile and region are required", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", args.hostname):
        print("refusing deployment: hostname must be a lowercase FQDN", file=sys.stderr)
        return 2
    if not re.fullmatch(r"ami-[0-9a-f]+", args.ami_id) or args.data_volume_size_gb < 20:
        print("refusing deployment: explicit AMI and at least 20 GiB are required", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    aws_dir = root / "infra" / "matrix" / "aws"
    try:
        if not deployment_profiles_installed():
            _run(
                (
                    sys.executable,
                    str(root / "scripts" / "bootstrap_fresh_synapse_account.py"),
                    "--profile",
                    EXPECTED_PROFILE,
                    "--region",
                    EXPECTED_REGION,
                ),
                cwd=root,
            )
        identity_raw = _run(
            ("aws", "sts", "get-caller-identity", "--output", "json"),
            cwd=root,
            profile=EXPECTED_DEPLOYMENT_PROFILE,
            capture=True,
        )
        identity = json.loads(identity_raw)
        if identity.get("Account") != EXPECTED_ACCOUNT or ":assumed-role/HyphaSynapseDeploymentRole/" not in identity.get("Arn", ""):
            raise DeploymentError("bounded deployment identity was not established")

        _write_configuration(aws_dir, args.hostname, args.ami_id, args.instance_type, args.data_volume_size_gb, True)
        _run(
            ("terraform", "init", "-reconfigure", "-backend-config=backend.hcl"),
            cwd=aws_dir,
            profile=EXPECTED_DEPLOYMENT_PROFILE,
        )
        with TemporaryDirectory(prefix="hypha-synapse-deploy-") as temporary_name:
            temporary = Path(temporary_name)
            state = _run(
                ("terraform", "state", "list"),
                cwd=aws_dir,
                profile=EXPECTED_DEPLOYMENT_PROFILE,
                capture=True,
            ).splitlines()
            if needs_base_stage(state):
                _write_configuration(
                    aws_dir,
                    args.hostname,
                    args.ami_id,
                    args.instance_type,
                    args.data_volume_size_gb,
                    False,
                )
                _plan_and_apply(aws_dir, temporary, "base", BASE_CREATES)
            if not _secret_has_current(root):
                _run(
                    (
                        sys.executable,
                        str(root / "scripts" / "populate_fresh_synapse_secret.py"),
                        "--profile",
                        EXPECTED_DEPLOYMENT_PROFILE,
                        "--region",
                        EXPECTED_REGION,
                    ),
                    cwd=root,
                    profile=EXPECTED_DEPLOYMENT_PROFILE,
                )
            _write_configuration(aws_dir, args.hostname, args.ami_id, args.instance_type, args.data_volume_size_gb, True)
            _plan_and_apply(aws_dir, temporary, "runtime", RUNTIME_CREATES)
        outputs = _safe_outputs(aws_dir)
        _verify_runtime(root, outputs["instance_id"])
    except (DeploymentError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "dns": {"type": "A", "name": args.hostname, "value": outputs["elastic_ip"]},
                "instance_id": outputs["instance_id"],
                "matrix_url": outputs["matrix_url"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
