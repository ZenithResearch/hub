#!/usr/bin/env python3
"""Validate redacted ISS-P14-007 Matrix production evidence artifacts.

The checker is intentionally local/static: it validates the shape and safety of an
operator-created evidence JSON file without requiring production credentials.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ISSUE = "ISS-P14-007"
MINIMUM_HEAD = "aa1bd8c"
SENSITIVE_KEY_RE = re.compile(
    r"(as[_-]?token|hs[_-]?token|access[_-]?token|bearer|authorization|registration[_-]?shared[_-]?secret|macaroon[_-]?secret|signing[_-]?key|raw[_-]?secret|tfvars|password|client[_-]?secret)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_RE = re.compile(
    r"(terraform\.tfvars|-----BEGIN [A-Z ]+PRIVATE KEY-----|Bearer\s+\S+|rev_[A-Za-z0-9_-]+|syt_[A-Za-z0-9_-]+|raw-[A-Za-z0-9_-]*(token|secret)|super-secret|prod(uction)?[_-]?(token|secret))",
    re.IGNORECASE,
)

class EvidenceError(Exception):
    pass

def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise EvidenceError(f"evidence file missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"invalid JSON evidence: {exc}") from exc
    if not isinstance(data, dict):
        raise EvidenceError("evidence root must be an object")
    return data

def as_obj(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise EvidenceError(f"missing required object: {key}")
    return value

def status(obj: dict[str, Any], path: str) -> str:
    value = obj.get("status")
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{path}.status is required")
    return value.lower()

def non_empty_list(obj: dict[str, Any], key: str, path: str) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list) or not value:
        raise EvidenceError(f"{path}.{key} must list tested/unproven restore evidence")
    return value

def walk_sensitive(value: Any, path: str = "$", findings: list[str] | None = None) -> list[str]:
    if findings is None:
        findings = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_str = str(key)
            child_path = f"{path}.{key_str}"
            if SENSITIVE_KEY_RE.search(key_str):
                findings.append(f"sensitive key at {child_path}")
            walk_sensitive(child, child_path, findings)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            walk_sensitive(child, f"{path}[{i}]", findings)
    elif isinstance(value, str):
        if SENSITIVE_VALUE_RE.search(value):
            findings.append(f"sensitive raw/tfvars/token value at {path}")
    return findings

def validate_source(data: dict[str, Any]) -> None:
    if data.get("issue") != ISSUE:
        raise EvidenceError(f"issue must be {ISSUE}")
    source = as_obj(data, "source")
    head = source.get("head") or source.get("head_sha")
    if not isinstance(head, str) or not head:
        raise EvidenceError("source head is required")
    if not head.startswith(MINIMUM_HEAD) and head < MINIMUM_HEAD:
        raise EvidenceError(f"source head must be at or after {MINIMUM_HEAD}")

def validate_terraform(data: dict[str, Any]) -> tuple[str, str]:
    terraform = as_obj(data, "terraform")
    plan = terraform.get("plan")
    apply = terraform.get("apply")
    if not isinstance(plan, dict):
        raise EvidenceError("plan evidence is required before apply evidence")
    if not isinstance(apply, dict):
        raise EvidenceError("apply evidence is required")
    plan_status = status(plan, "terraform.plan")
    apply_status = status(apply, "terraform.apply")
    if apply_status == "accepted" and plan_status != "accepted":
        raise EvidenceError("plan must be accepted before apply evidence can be accepted")
    return plan_status, apply_status

def validate_public_smoke(data: dict[str, Any]) -> str:
    smoke = as_obj(data, "public_smoke")
    smoke_status = status(smoke, "public_smoke")
    client = smoke.get("client_api")
    federation = smoke.get("federation_8448")
    errors: list[str] = []
    if not isinstance(client, dict):
        errors.append("public smoke requires client API status")
    if not isinstance(federation, dict):
        errors.append("public smoke requires federation 8448 status")
    if isinstance(client, dict):
        client_status = client.get("status")
        if smoke_status == "accepted" and client_status != 200:
            errors.append("client API status must be 200 for accepted public smoke")
    if isinstance(federation, dict):
        if smoke_status == "accepted" and federation.get("port") != 8448:
            errors.append("federation smoke must check port 8448")
        if smoke_status == "accepted" and federation.get("status") != 200:
            errors.append("federation 8448 status must be 200 for accepted public smoke")
    if errors:
        raise EvidenceError("; ".join(errors))
    return smoke_status

def validate_backup_restore(data: dict[str, Any]) -> str:
    backup = as_obj(data, "backup_restore")
    backup_status = status(backup, "backup_restore")
    non_empty_list(backup, "tested_restore_paths", "backup_restore")
    non_empty_list(backup, "unproven_restore_paths", "backup_restore")
    if backup_status == "accepted":
        errors: list[str] = []
        rpo_target = backup.get("rpo_target_hours")
        rds_age = backup.get("rds_recovery_point_age_hours")
        efs_age = backup.get("efs_recovery_point_age_hours")
        rto_target = backup.get("rto_target_minutes")
        elapsed = backup.get("start_to_usable_minutes")
        if rpo_target != 24:
            errors.append("backup_restore RPO target must be 24 hours")
        if not isinstance(rds_age, (int, float)) or rds_age > 24:
            errors.append("RDS recovery point must meet the 24-hour RPO")
        if not isinstance(efs_age, (int, float)) or efs_age > 24:
            errors.append("EFS recovery point must meet the 24-hour RPO")
        if rto_target != 120:
            errors.append("backup_restore RTO target must be 120 minutes")
        if not isinstance(elapsed, (int, float)) or elapsed > 120:
            errors.append("restore start-to-usable time must meet the 120-minute RTO")
        jobs = backup.get("restore_jobs")
        if not isinstance(jobs, dict) or jobs.get("rds") != "COMPLETED" or jobs.get("efs") != "COMPLETED":
            errors.append("RDS and EFS restore jobs must both be COMPLETED")
        if errors:
            raise EvidenceError("; ".join(errors))
    return backup_status

def validate_downstream(data: dict[str, Any], gates: list[str]) -> str:
    downstream = as_obj(data, "downstream")
    p15 = downstream.get("p15")
    if not isinstance(p15, dict):
        raise EvidenceError("downstream p15 lock/unlock state is required")
    p15_status = status(p15, "downstream.p15")
    all_accepted = all(g == "accepted" for g in gates)
    if p15_status == "unlocked" and not all_accepted:
        raise EvidenceError("p15 must remain locked until plan, apply, smoke, and backup evidence are accepted")
    return p15_status

def validate(data: dict[str, Any]) -> str:
    errors: list[str] = []
    findings = walk_sensitive(data)
    if findings:
        preview = "; ".join(findings[:5])
        errors.append(f"sensitive tfvars/token/raw material rejected: {preview}")
    try:
        validate_source(data)
    except EvidenceError as exc:
        errors.append(str(exc))
    try:
        plan_status, apply_status = validate_terraform(data)
    except EvidenceError as exc:
        errors.append(str(exc))
        raw_terraform = data.get("terraform")
        terraform = raw_terraform if isinstance(raw_terraform, dict) else {}
        raw_plan = terraform.get("plan")
        raw_apply = terraform.get("apply")
        plan = raw_plan if isinstance(raw_plan, dict) else {}
        apply = raw_apply if isinstance(raw_apply, dict) else {}
        plan_status = str(plan.get("status", "missing")).lower()
        apply_status = str(apply.get("status", "missing")).lower()
    try:
        smoke_status = validate_public_smoke(data)
    except EvidenceError as exc:
        errors.append(str(exc))
        raw_smoke = data.get("public_smoke")
        smoke = raw_smoke if isinstance(raw_smoke, dict) else {}
        smoke_status = str(smoke.get("status", "missing")).lower()
    try:
        backup_status = validate_backup_restore(data)
    except EvidenceError as exc:
        errors.append(str(exc))
        raw_backup = data.get("backup_restore")
        backup = raw_backup if isinstance(raw_backup, dict) else {}
        backup_status = str(backup.get("status", "missing")).lower()
    try:
        p15_status = validate_downstream(data, [plan_status, apply_status, smoke_status, backup_status])
    except EvidenceError as exc:
        errors.append(str(exc))
        p15_status = "invalid"
    if errors:
        raise EvidenceError("; ".join(errors))
    return f"accepted: {ISSUE} evidence valid; p15 {p15_status}"

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="validate an ISS-P14-007 evidence JSON artifact")
    v.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        message = validate(load_json(args.evidence))
    except EvidenceError as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
