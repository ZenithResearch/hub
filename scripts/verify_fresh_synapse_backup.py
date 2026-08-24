#!/usr/bin/env python3
"""Fail closed unless fresh Synapse backups and restore evidence are healthy."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

EXPECTED_PROFILE = "zenith-hypha-synapse"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_ROLE_ARN_FRAGMENT = "assumed-role/HyphaSynapseDeploymentRole/"
EXPECTED_INSTANCE_NAME = "hypha-fresh-synapse"
EXPECTED_DATA_VOLUME_NAME = "hypha-fresh-synapse-data"
EXPECTED_POLICY_DESCRIPTION = "Hypha fresh Synapse application-consistent EBS snapshots"
EXPECTED_DLM_ROLE_ARN = "arn:aws:iam::610992396917:role/HyphaSynapseDlmRole"
EXPECTED_DOCUMENT = "HyphaSynapseApplicationConsistentSnapshot"
EXPECTED_TARGET_TAGS = {
    "Name": EXPECTED_INSTANCE_NAME,
    "Project": "hypha",
    "Component": "fresh-synapse",
}
EXPECTED_SCHEDULES = {
    "Hourly application-consistent snapshots": ("hourly", 1, 72),
    "Daily restore-rehearsal snapshots": ("daily", 24, 35),
}
INSTANCE_PATTERN = re.compile(r"i-[0-9a-f]{8,17}")
POLICY_PATTERN = re.compile(r"policy-[0-9a-f]+")
HOURLY_MAX_AGE = timedelta(hours=3)
DAILY_MAX_AGE = timedelta(hours=26)
RESTORE_EVIDENCE_MAX_AGE = timedelta(days=30)


class BackupVerificationError(RuntimeError):
    """A fail-closed recovery-gate error with no workload data."""


RunAws = Callable[[Sequence[str]], str]


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_STS",
        "AWS_ENDPOINT_URL_EC2",
        "AWS_ENDPOINT_URL_DLM",
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
        raise BackupVerificationError("AWS CLI execution failed") from exc
    if process.returncode != 0:
        raise BackupVerificationError("AWS CLI command failed")
    return process.stdout


def _json_object(raw: str, description: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BackupVerificationError("AWS returned invalid " + description) from exc
    if not isinstance(value, dict):
        raise BackupVerificationError("AWS returned invalid " + description)
    return value


def _tags(resource: Mapping[str, Any]) -> dict[str, str]:
    raw_tags = resource.get("Tags")
    if not isinstance(raw_tags, list):
        raise BackupVerificationError("AWS returned invalid resource tags")
    tags: dict[str, str] = {}
    for item in raw_tags:
        if not isinstance(item, dict):
            raise BackupVerificationError("AWS returned invalid resource tags")
        key = item.get("Key")
        value = item.get("Value")
        if not isinstance(key, str) or not isinstance(value, str) or key in tags:
            raise BackupVerificationError("AWS returned invalid resource tags")
        tags[key] = value
    return tags


def _utc_timestamp(value: object, description: str) -> datetime:
    if not isinstance(value, str):
        raise BackupVerificationError("AWS returned invalid " + description)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackupVerificationError("AWS returned invalid " + description) from exc
    if parsed.tzinfo is None:
        raise BackupVerificationError("AWS returned invalid " + description)
    return parsed.astimezone(timezone.utc)


def verify_role(run_aws: RunAws = _run_aws) -> None:
    identity = _json_object(
        run_aws(("sts", "get-caller-identity", "--output", "json")),
        "caller identity",
    )
    arn = identity.get("Arn")
    if (
        identity.get("Account") != EXPECTED_ACCOUNT
        or not isinstance(arn, str)
        or EXPECTED_ROLE_ARN_FRAGMENT not in arn
    ):
        raise BackupVerificationError("bounded deployment identity was not established")


def _instance_inventory(instance_id: str, run_aws: RunAws) -> dict[str, str]:
    response = _json_object(
        run_aws(("ec2", "describe-instances", "--instance-ids", instance_id, "--output", "json")),
        "instance inventory",
    )
    reservations = response.get("Reservations")
    if not isinstance(reservations, list) or len(reservations) != 1:
        raise BackupVerificationError("exact Synapse instance was not found")
    instances = reservations[0].get("Instances") if isinstance(reservations[0], dict) else None
    if not isinstance(instances, list) or len(instances) != 1 or not isinstance(instances[0], dict):
        raise BackupVerificationError("exact Synapse instance was not found")
    instance = instances[0]
    instance_tags = _tags(instance)
    state = instance.get("State")
    if (
        instance.get("InstanceId") != instance_id
        or not isinstance(state, dict)
        or state.get("Name") != "running"
        or instance_tags.get("Name") != EXPECTED_INSTANCE_NAME
        or instance_tags.get("Project") != "hypha"
        or instance_tags.get("Component") != "fresh-synapse"
    ):
        raise BackupVerificationError("exact running Synapse instance was not found")
    placement = instance.get("Placement")
    availability_zone = placement.get("AvailabilityZone") if isinstance(placement, dict) else None
    root_device = instance.get("RootDeviceName")
    mappings = instance.get("BlockDeviceMappings")
    if not isinstance(availability_zone, str) or not isinstance(root_device, str):
        raise BackupVerificationError("AWS returned invalid instance storage metadata")
    if not isinstance(mappings, list) or len(mappings) < 2:
        raise BackupVerificationError("Synapse instance storage is incomplete")
    volumes_by_device: dict[str, str] = {}
    for mapping in mappings:
        ebs = mapping.get("Ebs") if isinstance(mapping, dict) else None
        device = mapping.get("DeviceName") if isinstance(mapping, dict) else None
        volume_id = ebs.get("VolumeId") if isinstance(ebs, dict) else None
        if not isinstance(device, str) or not isinstance(volume_id, str):
            raise BackupVerificationError("AWS returned invalid instance storage metadata")
        volumes_by_device[device] = volume_id
    root_volume_id = volumes_by_device.get(root_device)
    if not isinstance(root_volume_id, str):
        raise BackupVerificationError("Synapse root volume was not found")
    response = _json_object(
        run_aws(
            (
                "ec2",
                "describe-volumes",
                "--volume-ids",
                *sorted(volumes_by_device.values()),
                "--output",
                "json",
            )
        ),
        "volume inventory",
    )
    volumes = response.get("Volumes")
    if not isinstance(volumes, list) or len(volumes) != len(volumes_by_device):
        raise BackupVerificationError("Synapse volume inventory is incomplete")
    data_volume_id: str | None = None
    for volume in volumes:
        if not isinstance(volume, dict) or volume.get("Encrypted") is not True:
            raise BackupVerificationError("Synapse storage must be encrypted")
        volume_id = volume.get("VolumeId")
        if not isinstance(volume_id, str):
            raise BackupVerificationError("AWS returned invalid volume inventory")
        volume_tags = _tags(volume)
        if volume_tags.get("Name") == EXPECTED_DATA_VOLUME_NAME:
            if data_volume_id is not None:
                raise BackupVerificationError("multiple Synapse data volumes were found")
            if (
                volume_tags.get("Project") != "hypha"
                or volume_tags.get("Component") != "fresh-synapse"
            ):
                raise BackupVerificationError("Synapse data volume tags are invalid")
            data_volume_id = volume_id
    if data_volume_id is None:
        raise BackupVerificationError("Synapse data volume was not found")
    if set(volumes_by_device.values()) != {root_volume_id, data_volume_id}:
        raise BackupVerificationError("unexpected volume remains attached to Synapse")
    return {
        "availability_zone": availability_zone,
        "data_volume_id": data_volume_id,
        "root_volume_id": root_volume_id,
    }


def _script_is_exact(script: object) -> bool:
    if not isinstance(script, dict):
        return False
    handler = script.get("ExecutionHandler")
    return (
        isinstance(handler, str)
        and (handler == EXPECTED_DOCUMENT or handler.endswith("/" + EXPECTED_DOCUMENT))
        and script.get("ExecutionHandlerService") == "AWS_SYSTEMS_MANAGER"
        and script.get("ExecuteOperationOnScriptFailure") is False
        and script.get("ExecutionTimeout") == 120
        and script.get("MaximumRetryCount") == 3
        and set(script.get("Stages", [])) == {"PRE", "POST"}
    )


def _policy(run_aws: RunAws) -> str:
    response = _json_object(
        run_aws(
            (
                "dlm",
                "get-lifecycle-policies",
                "--resource-types",
                "INSTANCE",
                "--state",
                "ENABLED",
                "--output",
                "json",
            )
        ),
        "backup policy list",
    )
    policies = response.get("Policies")
    matches = (
        [
            item
            for item in policies
            if isinstance(item, dict) and item.get("Description") == EXPECTED_POLICY_DESCRIPTION
        ]
        if isinstance(policies, list)
        else []
    )
    if len(matches) != 1 or matches[0].get("State") != "ENABLED":
        raise BackupVerificationError("exact enabled Synapse backup policy was not found")
    policy_id = matches[0].get("PolicyId")
    if not isinstance(policy_id, str) or not POLICY_PATTERN.fullmatch(policy_id):
        raise BackupVerificationError("AWS returned invalid backup policy metadata")
    response = _json_object(
        run_aws(("dlm", "get-lifecycle-policy", "--policy-id", policy_id, "--output", "json")),
        "backup policy",
    )
    policy = response.get("Policy")
    if not isinstance(policy, dict) or policy.get("State") != "ENABLED":
        raise BackupVerificationError("Synapse backup policy is not enabled")
    if policy.get("ExecutionRoleArn") != EXPECTED_DLM_ROLE_ARN:
        raise BackupVerificationError("Synapse backup policy role is invalid")
    details = policy.get("PolicyDetails")
    if not isinstance(details, dict):
        raise BackupVerificationError("AWS returned invalid backup policy")
    if details.get("PolicyType") != "EBS_SNAPSHOT_MANAGEMENT" or details.get("ResourceTypes") != [
        "INSTANCE"
    ]:
        raise BackupVerificationError("Synapse backup policy target type is invalid")
    target_tags = details.get("TargetTags")
    if not isinstance(target_tags, list) or any(not isinstance(item, dict) for item in target_tags):
        raise BackupVerificationError("Synapse backup policy target is invalid")
    target_tag_map = {item.get("Key"): item.get("Value") for item in target_tags}
    if len(target_tag_map) != len(target_tags) or target_tag_map != EXPECTED_TARGET_TAGS:
        raise BackupVerificationError("Synapse backup policy target is invalid")
    parameters = details.get("Parameters")
    if not isinstance(parameters, dict) or parameters.get("ExcludeBootVolume") is not False:
        raise BackupVerificationError("Synapse backup policy must include the root volume")
    schedules = details.get("Schedules")
    if not isinstance(schedules, list) or len(schedules) != len(EXPECTED_SCHEDULES):
        raise BackupVerificationError("Synapse backup schedules are invalid")
    by_name = {item.get("Name"): item for item in schedules if isinstance(item, dict)}
    if set(by_name) != set(EXPECTED_SCHEDULES):
        raise BackupVerificationError("Synapse backup schedules are invalid")
    for name, (backup_class, interval, retention) in EXPECTED_SCHEDULES.items():
        schedule = by_name[name]
        create_rule = schedule.get("CreateRule")
        retain_rule = schedule.get("RetainRule")
        tags = schedule.get("Tags")
        scripts = create_rule.get("Scripts") if isinstance(create_rule, dict) else None
        if (
            schedule.get("CopyTags") is not True
            or tags != [{"Key": "HyphaBackupClass", "Value": backup_class}]
            or not isinstance(create_rule, dict)
            or create_rule.get("Interval") != interval
            or create_rule.get("IntervalUnit") != "HOURS"
            or not isinstance(scripts, list)
            or len(scripts) != 1
            or not _script_is_exact(scripts[0])
            or retain_rule != {"Count": retention}
        ):
            raise BackupVerificationError("Synapse backup schedule contract is invalid")
    return policy_id


def _snapshot_age(snapshot: Mapping[str, Any], now: datetime) -> timedelta:
    started = _utc_timestamp(snapshot.get("StartTime"), "snapshot timestamp")
    age = now - started
    if age < timedelta(minutes=-5):
        raise BackupVerificationError("snapshot timestamp is in the future")
    return age


def _application_consistent(snapshot: Mapping[str, Any], backup_class: str) -> bool:
    tags = _tags(snapshot)
    return (
        snapshot.get("State") == "completed"
        and tags.get("HyphaBackupClass") == backup_class
        and tags.get("aws:dlm:pre-script") == "SUCCESS"
        and tags.get("aws:dlm:post-script") == "SUCCESS"
    )


def _fresh_snapshot(
    snapshots: Sequence[Mapping[str, Any]],
    volume_id: str,
    backup_class: str,
    maximum_age: timedelta,
    now: datetime,
) -> Mapping[str, Any]:
    candidates = [
        item
        for item in snapshots
        if item.get("VolumeId") == volume_id and _application_consistent(item, backup_class)
    ]
    if not candidates:
        raise BackupVerificationError("application-consistent Synapse snapshot is missing")
    latest = min(candidates, key=lambda item: _snapshot_age(item, now))
    if _snapshot_age(latest, now) > maximum_age:
        raise BackupVerificationError("application-consistent Synapse snapshot is stale")
    return latest


def verify_backup(
    instance_id: str,
    run_aws: RunAws = _run_aws,
    *,
    require_restore: bool = True,
    now: datetime | None = None,
) -> dict[str, str]:
    if not INSTANCE_PATTERN.fullmatch(instance_id):
        raise BackupVerificationError("an explicit EC2 instance ID is required")
    checked_at = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    inventory = _instance_inventory(instance_id, run_aws)
    policy_id = _policy(run_aws)
    response = _json_object(
        run_aws(
            (
                "ec2",
                "describe-snapshots",
                "--owner-ids",
                "self",
                "--filters",
                "Name=tag:aws:dlm:lifecycle-policy-id,Values=" + policy_id,
                "--output",
                "json",
            )
        ),
        "snapshot inventory",
    )
    raw_snapshots = response.get("Snapshots")
    if not isinstance(raw_snapshots, list) or any(
        not isinstance(item, dict) for item in raw_snapshots
    ):
        raise BackupVerificationError("AWS returned invalid snapshot inventory")
    snapshots: list[Mapping[str, Any]] = raw_snapshots
    result = {
        "availability_zone": inventory["availability_zone"],
        "backup_policy_id": policy_id,
        "data_volume_id": inventory["data_volume_id"],
        "instance_id": instance_id,
        "root_volume_id": inventory["root_volume_id"],
        "status": "backup_verified",
    }
    daily_data: Mapping[str, Any] | None = None
    for label, volume_id in (
        ("root", inventory["root_volume_id"]),
        ("data", inventory["data_volume_id"]),
    ):
        hourly = _fresh_snapshot(snapshots, volume_id, "hourly", HOURLY_MAX_AGE, checked_at)
        daily = _fresh_snapshot(snapshots, volume_id, "daily", DAILY_MAX_AGE, checked_at)
        for backup_class, snapshot in (("hourly", hourly), ("daily", daily)):
            snapshot_id = snapshot.get("SnapshotId")
            if not isinstance(snapshot_id, str):
                raise BackupVerificationError("AWS returned invalid snapshot metadata")
            result[f"{label}_{backup_class}_snapshot_id"] = snapshot_id
        if label == "data":
            daily_data = daily
    if daily_data is None:
        raise BackupVerificationError("daily Synapse data snapshot is missing")
    evidence: list[tuple[timedelta, Mapping[str, Any]]] = []
    for snapshot in snapshots:
        if snapshot.get("VolumeId") != inventory["data_volume_id"] or not _application_consistent(
            snapshot, "daily"
        ):
            continue
        tags = _tags(snapshot)
        if tags.get("HyphaRestoreVerifierVersion") != "1":
            continue
        try:
            age = checked_at - _utc_timestamp(
                tags.get("HyphaRestoreVerifiedAt"), "restore evidence timestamp"
            )
        except BackupVerificationError:
            continue
        if timedelta(0) <= age <= RESTORE_EVIDENCE_MAX_AGE:
            evidence.append((age, snapshot))
    if require_restore:
        if not evidence:
            raise BackupVerificationError("recent isolated restore evidence is missing")
        restore_snapshot_id = min(evidence, key=lambda item: item[0])[1].get("SnapshotId")
        if not isinstance(restore_snapshot_id, str):
            raise BackupVerificationError("AWS returned invalid restore evidence")
        result["restore_verified_snapshot_id"] = restore_snapshot_id
        result["status"] = "backup_and_restore_verified"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--instance-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing verification: exact profile and region are required", file=sys.stderr)
        return 2
    try:
        verify_role()
        result = verify_backup(args.instance_id)
    except BackupVerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
