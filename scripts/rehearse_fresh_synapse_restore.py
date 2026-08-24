#!/usr/bin/env python3
"""Restore a Synapse data snapshot in isolation and record bounded evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import verify_fresh_synapse_backup as backup

EXPECTED_PROFILE = backup.EXPECTED_PROFILE
EXPECTED_REGION = backup.EXPECTED_REGION
POSTGRES_IMAGE = (
    "postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297"
)
VOLUME_PATTERN = re.compile(r"vol-[0-9a-f]{8,17}")
SNAPSHOT_PATTERN = re.compile(r"snap-[0-9a-f]{8,17}")


class RestoreRehearsalError(RuntimeError):
    """A fail-closed restore error containing no workload data."""


def _json_object(raw: str, description: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RestoreRehearsalError("AWS returned invalid " + description) from exc
    if not isinstance(value, dict):
        raise RestoreRehearsalError("AWS returned invalid " + description)
    return value


def restore_commands(volume_id: str) -> tuple[str, ...]:
    if not VOLUME_PATTERN.fullmatch(volume_id):
        raise RestoreRehearsalError("AWS returned invalid restore volume metadata")
    compact_volume_id = volume_id.replace("-", "")
    return (
        "set -euo pipefail",
        "umask 077",
        f"VOLUME_ID='{volume_id}'",
        f"EXPECTED_DEVICE='/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_{compact_volume_id}'",
        "RESTORE_MOUNT=/mnt/hypha-restore-rehearsal",
        "RESTORE_CONTAINER=hypha-postgres-restore-rehearsal",
        'cleanup() { set +e; docker stop --time 30 "$RESTORE_CONTAINER" >/dev/null 2>&1; docker rm --force "$RESTORE_CONTAINER" >/dev/null 2>&1; mountpoint -q "$RESTORE_MOUNT" && umount "$RESTORE_MOUNT"; rmdir "$RESTORE_MOUNT" 2>/dev/null || true; }',
        "trap cleanup EXIT",
        "export EXPECTED_DEVICE",
        "timeout 120 bash -c 'until [ -e \"$EXPECTED_DEVICE\" ]; do sleep 2; done'",
        'RESTORE_DEVICE=$(readlink -f "$EXPECTED_DEVICE")',
        'test -b "$RESTORE_DEVICE"',
        'test "$(blkid -s TYPE -o value "$RESTORE_DEVICE")" = xfs',
        'test "$(blkid -s LABEL -o value "$RESTORE_DEVICE")" = hypha-matrix',
        'mkdir -p "$RESTORE_MOUNT"',
        'mount -t xfs -o nouuid,nodev,nosuid,noexec "$RESTORE_DEVICE" "$RESTORE_MOUNT"',
        'test -d "$RESTORE_MOUNT/postgres/base"',
        'test -s "$RESTORE_MOUNT/synapse/server.signing.key"',
        'test -d "$RESTORE_MOUNT/synapse/media_store"',
        f"docker pull '{POSTGRES_IMAGE}' >/dev/null",
        f'docker run --detach --name "$RESTORE_CONTAINER" --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m --tmpfs /var/run/postgresql:rw,noexec,nosuid,size=16m --mount type=bind,src="$RESTORE_MOUNT/postgres",dst=/var/lib/postgresql/data \'{POSTGRES_IMAGE}\' >/dev/null',
        'for attempt in $(seq 1 60); do docker exec "$RESTORE_CONTAINER" pg_isready --username synapse --dbname synapse >/dev/null 2>&1 && break; [ "$attempt" -lt 60 ] || { echo \'restored PostgreSQL did not become ready\' >&2; exit 1; }; sleep 5; done',
        "test \"$(docker exec \"$RESTORE_CONTAINER\" psql --username synapse --dbname synapse --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 --command=\"SELECT COUNT(*) = 3 FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('users', 'rooms', 'events');\")\" = t",
        'docker stop --time 60 "$RESTORE_CONTAINER" >/dev/null',
        'docker rm "$RESTORE_CONTAINER" >/dev/null',
        'umount "$RESTORE_MOUNT"',
        'rmdir "$RESTORE_MOUNT"',
        "trap - EXIT",
        'printf "%s\\n" "isolated Synapse restore verified"',
    )


def _send_command(instance_id: str, commands: Sequence[str]) -> str:
    with TemporaryDirectory(prefix="hypha-restore-rehearsal-") as temporary_name:
        parameters = Path(temporary_name) / "parameters.json"
        parameters.write_text(json.dumps({"commands": list(commands)}), encoding="utf-8")
        os.chmod(parameters, 0o600)
        response = _json_object(
            backup._run_aws(  # noqa: SLF001
                (
                    "ssm",
                    "send-command",
                    "--instance-ids",
                    instance_id,
                    "--document-name",
                    "AWS-RunShellScript",
                    "--comment",
                    "Verify isolated Hypha Synapse snapshot restore",
                    "--parameters",
                    "file://" + str(parameters),
                    "--timeout-seconds",
                    "900",
                    "--output",
                    "json",
                )
            ),
            "restore command metadata",
        )
    command = response.get("Command")
    command_id = command.get("CommandId") if isinstance(command, dict) else None
    if not isinstance(command_id, str) or not command_id:
        raise RestoreRehearsalError("AWS returned invalid restore command metadata")
    return command_id


def _wait_for_command(command_id: str) -> None:
    deadline = time.monotonic() + 1_020
    while time.monotonic() < deadline:
        response = _json_object(
            backup._run_aws(  # noqa: SLF001
                ("ssm", "list-commands", "--command-id", command_id, "--output", "json")
            ),
            "restore command status",
        )
        commands = response.get("Commands")
        status = commands[0].get("Status") if isinstance(commands, list) and commands else None
        if status == "Success":
            return
        if status in {"Cancelled", "Failed", "TimedOut"}:
            raise RestoreRehearsalError("isolated restore verification failed")
        time.sleep(5)
    raise RestoreRehearsalError("isolated restore verification timed out")


def _wait_for_terminal_cleanup(command_id: str) -> None:
    deadline = time.monotonic() + 1_020
    while time.monotonic() < deadline:
        try:
            response = _json_object(
                backup._run_aws(  # noqa: SLF001
                    ("ssm", "list-commands", "--command-id", command_id, "--output", "json")
                ),
                "restore command cleanup status",
            )
        except (backup.BackupVerificationError, RestoreRehearsalError):
            time.sleep(5)
            continue
        commands = response.get("Commands")
        status = commands[0].get("Status") if isinstance(commands, list) and commands else None
        if status in {"Cancelled", "Failed", "Success", "TimedOut"}:
            return
        time.sleep(5)
    raise RestoreRehearsalError("restore command did not reach a terminal cleanup state")


def _create_restore_volume(availability_zone: str, snapshot_id: str) -> str:
    if not SNAPSHOT_PATTERN.fullmatch(snapshot_id):
        raise RestoreRehearsalError("AWS returned invalid snapshot metadata")
    tag_specification = (
        "ResourceType=volume,Tags=["
        "{Key=Name,Value=hypha-fresh-synapse-restore},"
        "{Key=Project,Value=hypha},"
        "{Key=Component,Value=fresh-synapse},"
        "{Key=ManagedBy,Value=restore-rehearsal},"
        "{Key=Purpose,Value=restore-rehearsal}]"
    )
    response = _json_object(
        backup._run_aws(  # noqa: SLF001
            (
                "ec2",
                "create-volume",
                "--availability-zone",
                availability_zone,
                "--snapshot-id",
                snapshot_id,
                "--volume-type",
                "gp3",
                "--encrypted",
                "--tag-specifications",
                tag_specification,
                "--output",
                "json",
            )
        ),
        "restore volume metadata",
    )
    volume_id = response.get("VolumeId")
    if not isinstance(volume_id, str) or not VOLUME_PATTERN.fullmatch(volume_id):
        raise RestoreRehearsalError("AWS returned invalid restore volume metadata")
    backup._run_aws(("ec2", "wait", "volume-available", "--volume-ids", volume_id))  # noqa: SLF001
    return volume_id


def _attach_restore_volume(instance_id: str, volume_id: str) -> None:
    backup._run_aws(  # noqa: SLF001
        (
            "ec2",
            "attach-volume",
            "--device",
            "/dev/sdg",
            "--instance-id",
            instance_id,
            "--volume-id",
            volume_id,
            "--output",
            "json",
        )
    )
    backup._run_aws(("ec2", "wait", "volume-in-use", "--volume-ids", volume_id))  # noqa: SLF001


def _delete_restore_volume(instance_id: str, volume_id: str) -> None:
    response = _json_object(
        backup._run_aws(  # noqa: SLF001
            ("ec2", "describe-volumes", "--volume-ids", volume_id, "--output", "json")
        ),
        "restore volume attachment metadata",
    )
    volumes = response.get("Volumes")
    if not isinstance(volumes, list) or len(volumes) != 1 or not isinstance(volumes[0], dict):
        raise RestoreRehearsalError("AWS returned invalid restore volume attachment metadata")
    attachments = volumes[0].get("Attachments")
    if not isinstance(attachments, list) or len(attachments) > 1:
        raise RestoreRehearsalError("AWS returned invalid restore volume attachment metadata")
    if attachments:
        attachment = attachments[0]
        if not isinstance(attachment, dict) or attachment.get("InstanceId") != instance_id:
            raise RestoreRehearsalError("restore volume has an unexpected attachment")
        backup._run_aws(  # noqa: SLF001
            (
                "ec2",
                "detach-volume",
                "--instance-id",
                instance_id,
                "--volume-id",
                volume_id,
            )
        )
        backup._run_aws(("ec2", "wait", "volume-available", "--volume-ids", volume_id))  # noqa: SLF001
    backup._run_aws(("ec2", "delete-volume", "--volume-id", volume_id))  # noqa: SLF001
    backup._run_aws(("ec2", "wait", "volume-deleted", "--volume-ids", volume_id))  # noqa: SLF001


def _record_restore_evidence(snapshot_id: str) -> str:
    verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    backup._run_aws(  # noqa: SLF001
        (
            "ec2",
            "create-tags",
            "--resources",
            snapshot_id,
            "--tags",
            "Key=HyphaRestoreVerifiedAt,Value=" + verified_at,
            "Key=HyphaRestoreVerifierVersion,Value=1",
        )
    )
    return verified_at


def rehearse_restore(instance_id: str) -> dict[str, str]:
    verification = backup.verify_backup(instance_id, require_restore=False)
    snapshot_id = verification["data_daily_snapshot_id"]
    volume_id: str | None = None
    command_id: str | None = None
    command_dispatch_attempted = False
    failure: BaseException | None = None
    try:
        volume_id = _create_restore_volume(verification["availability_zone"], snapshot_id)
        _attach_restore_volume(instance_id, volume_id)
        command_dispatch_attempted = True
        command_id = _send_command(instance_id, restore_commands(volume_id))
        _wait_for_command(command_id)
    except (backup.BackupVerificationError, RestoreRehearsalError) as exc:
        failure = exc
    finally:
        if volume_id is not None:
            try:
                if command_dispatch_attempted and command_id is None:
                    raise RestoreRehearsalError("restore command identity was not established")
                if command_id is not None:
                    _wait_for_terminal_cleanup(command_id)
                _delete_restore_volume(instance_id, volume_id)
            except (backup.BackupVerificationError, RestoreRehearsalError) as cleanup_error:
                raise RestoreRehearsalError("restore rehearsal cleanup failed") from cleanup_error
    if failure is not None:
        raise RestoreRehearsalError("isolated restore verification failed") from failure
    verified_at = _record_restore_evidence(snapshot_id)
    result = backup.verify_backup(instance_id, require_restore=True)
    return {
        "backup_policy_id": result["backup_policy_id"],
        "instance_id": instance_id,
        "restore_snapshot_id": snapshot_id,
        "restore_verified_at": verified_at,
        "status": "isolated_restore_verified",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing rehearsal: exact profile and region are required", file=sys.stderr)
        return 2
    try:
        backup.verify_role()
        if args.dry_run:
            verification = backup.verify_backup(args.instance_id, require_restore=False)
            print(
                json.dumps(
                    {
                        "instance_id": args.instance_id,
                        "restore_snapshot_id": verification["data_daily_snapshot_id"],
                        "status": "restore_rehearsal_planned",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        result = rehearse_restore(args.instance_id)
    except (backup.BackupVerificationError, RestoreRehearsalError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
