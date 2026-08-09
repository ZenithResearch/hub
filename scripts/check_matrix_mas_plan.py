#!/usr/bin/env python3
"""Fail-closed policy check for reviewed Matrix MAS Terraform plan JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PHASE_ALLOWLISTS = {
    "infrastructure": (
        "matrix_mas",
        "aws_backup_selection.matrix",
    ),
    "public-edge": (
        "matrix_mas",
        "aws_security_group.alb",
    ),
    "cutover": (
        "matrix_mas",
        "aws_ecs_task_definition.matrix_synapse",
        "aws_ecs_service.matrix_synapse",
        "aws_iam_role_policy.execution_secrets",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=sorted(PHASE_ALLOWLISTS), required=True)
    parser.add_argument("plan_json", type=Path)
    args = parser.parse_args()

    plan = json.loads(args.plan_json.read_text(encoding="utf-8"))
    allowed = PHASE_ALLOWLISTS[args.phase]
    rejected: list[tuple[str, list[str]]] = []

    for resource in plan.get("resource_changes", []):
        address = resource.get("address", "")
        actions = resource.get("change", {}).get("actions", [])
        if actions in ([], ["no-op"], ["read"]):
            continue
        if any(token in address for token in allowed):
            continue
        rejected.append((address, actions))

    if rejected:
        print(f"MAS {args.phase} plan rejected; unrelated or protected resources change:")
        for address, actions in rejected:
            print(f"- {address}: {','.join(actions)}")
        return 1

    print(f"MAS {args.phase} plan accepted by address policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
