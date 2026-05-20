#!/usr/bin/env python3
"""Stage the configured llama-server GGUF model through S3 into EFS.

This script intentionally avoids local Docker builds and does not print secrets.
It can optionally upload a local model file to the Terraform-configured private
S3 bucket, then runs the Terraform-defined one-shot ECS preload task that mounts
Frank EFS writable and copies the model into /models/llama/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_REGION = "us-east-1"
DEFAULT_PROFILE = "zenith-hermes"
DEFAULT_TF_DIR = "infra/aws_baseline_80"


def run(cmd: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)
    return proc


def terraform_output(workdir: Path, name: str) -> Any:
    proc = run(["terraform", "-chdir=" + DEFAULT_TF_DIR, "output", "-json", name], cwd=workdir)
    return json.loads(proc.stdout)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def aws_base(profile: str | None, region: str) -> list[str]:
    cmd = ["aws"]
    if profile:
        cmd.extend(["--profile", profile])
    cmd.extend(["--region", region])
    return cmd


def upload_model(workdir: Path, profile: str | None, region: str, local_path: Path, bucket: str, key: str, expected_sha256: str | None) -> str:
    if not local_path.is_file():
        raise SystemExit(f"model file not found: {local_path}")
    actual = sha256_file(local_path)
    if expected_sha256 and actual != expected_sha256:
        raise SystemExit(f"local SHA256 mismatch for {local_path.name}: expected {expected_sha256}, got {actual}")
    print(f"local_sha256={actual}")
    dest = f"s3://{bucket}/{key}"
    print(f"uploading {local_path} -> {dest}")
    run(aws_base(profile, region) + ["s3", "cp", str(local_path), dest, "--no-progress"], cwd=workdir)
    return actual


def run_preload_task(workdir: Path, profile: str | None, region: str, *, cluster: str, task_definition: str, subnets: list[str], security_group: str) -> str:
    network = "awsvpcConfiguration={subnets=[%s],securityGroups=[%s],assignPublicIp=DISABLED}" % (
        ",".join(subnets),
        security_group,
    )
    print(f"running preload task_definition={task_definition}")
    proc = run(
        aws_base(profile, region)
        + [
            "ecs",
            "run-task",
            "--cluster",
            cluster,
            "--launch-type",
            "FARGATE",
            "--task-definition",
            task_definition,
            "--network-configuration",
            network,
            "--count",
            "1",
            "--output",
            "json",
        ],
        cwd=workdir,
    )
    data = json.loads(proc.stdout)
    failures = data.get("failures") or []
    if failures:
        raise SystemExit(f"run-task failures: {json.dumps(failures)}")
    tasks = data.get("tasks") or []
    if not tasks:
        raise SystemExit("run-task returned no tasks")
    task_arn = tasks[0]["taskArn"]
    print(f"task_arn={task_arn}")
    return task_arn


def wait_for_task(workdir: Path, profile: str | None, region: str, cluster: str, task_arn: str, timeout_s: int) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        proc = run(
            aws_base(profile, region)
            + [
                "ecs",
                "describe-tasks",
                "--cluster",
                cluster,
                "--tasks",
                task_arn,
                "--output",
                "json",
            ],
            cwd=workdir,
        )
        data = json.loads(proc.stdout)
        tasks = data.get("tasks") or []
        if not tasks:
            raise SystemExit("describe-tasks returned no task")
        last = tasks[0]
        status = last.get("lastStatus")
        print(f"task_status={status}")
        if status == "STOPPED":
            containers = last.get("containers") or []
            exit_codes = [c.get("exitCode") for c in containers]
            stopped_reason = last.get("stoppedReason")
            print(f"stopped_reason={stopped_reason}")
            print(f"container_exit_codes={exit_codes}")
            if any(code != 0 for code in exit_codes):
                raise SystemExit(f"preload task failed: {json.dumps({'stoppedReason': stopped_reason, 'containers': containers}, default=str)}")
            return last
        time.sleep(10)
    raise SystemExit(f"timed out waiting for preload task after {timeout_s}s; last={json.dumps(last, default=str)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload and stage llama-server GGUF from S3 into EFS using the Terraform-defined ECS preload task.")
    parser.add_argument("--workdir", default=".", help="Hub repo/worktree root. Defaults to current directory.")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", DEFAULT_PROFILE), help="AWS profile. Set empty string to use ambient credentials.")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", DEFAULT_REGION))
    parser.add_argument("--upload-local", help="Optional local GGUF path to upload to the configured S3 key before preloading.")
    parser.add_argument("--expected-sha256", default="", help="Optional SHA256 to verify locally before upload and in the ECS preload task.")
    parser.add_argument("--timeout-s", type=int, default=1800, help="Preload task timeout in seconds.")
    parser.add_argument("--skip-run-task", action="store_true", help="Only upload local file; do not run the ECS preload task.")
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    profile = args.profile or None
    region = args.region

    cluster = terraform_output(workdir, "ecs_cluster_name")
    task_definition = terraform_output(workdir, "llama_model_preload_task_definition_arn")
    subnets = terraform_output(workdir, "private_subnet_ids")
    security_group = terraform_output(workdir, "llama_server_security_group_id")
    bucket = terraform_output(workdir, "llama_server_model_bucket_name")
    key = terraform_output(workdir, "llama_server_model_s3_key")
    model_name = terraform_output(workdir, "llama_server_model_name")

    print(f"cluster={cluster}")
    print(f"bucket={bucket}")
    print(f"key={key}")
    print(f"model_name={model_name}")

    expected_sha256 = args.expected_sha256.strip() or None
    if args.upload_local:
        upload_model(workdir, profile, region, Path(args.upload_local).expanduser().resolve(), bucket, key, expected_sha256)

    if args.skip_run_task:
        return 0

    task_arn = run_preload_task(
        workdir,
        profile,
        region,
        cluster=cluster,
        task_definition=task_definition,
        subnets=subnets,
        security_group=security_group,
    )
    wait_for_task(workdir, profile, region, cluster, task_arn, args.timeout_s)
    print("preload_complete=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
