#!/usr/bin/env python3
"""Fail-closed policy check for reviewed Matrix MAS Terraform plan JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


CREATE = ("create",)
UPDATE = ("update",)
REPLACE = ("delete", "create")

# Rules are exact Terraform address globs paired with the only action sequences
# acceptable in that rollout phase. A MAS-looking substring is never sufficient.
PHASE_RULES = {
    "infrastructure": {
        "aws_acm_certificate.matrix_mas[0]": {CREATE},
        "aws_backup_selection.matrix[0]": {CREATE, UPDATE, REPLACE},
        "aws_cloudwatch_log_group.matrix_mas[0]": {CREATE},
        "aws_cloudwatch_metric_alarm.matrix_mas_rds_cpu[0]": {CREATE, UPDATE},
        "aws_cloudwatch_metric_alarm.matrix_mas_rds_free_storage[0]": {CREATE, UPDATE},
        "aws_db_instance.matrix_mas[0]": {CREATE, UPDATE},
        "aws_ecs_task_definition.matrix_mas[0]": {CREATE},
        "aws_iam_role.matrix_mas_execution[0]": {CREATE},
        "aws_iam_role.matrix_mas_task[0]": {CREATE},
        "aws_iam_role_policy.matrix_mas_execution_secrets[0]": {CREATE, UPDATE},
        "aws_iam_role_policy_attachment.matrix_mas_execution[0]": {CREATE},
        "aws_lb_target_group.matrix_mas[0]": {CREATE, UPDATE},
        "aws_secretsmanager_secret.matrix_mas_encryption_secret": {CREATE, UPDATE},
        "aws_secretsmanager_secret.matrix_mas_signing_key": {CREATE, UPDATE},
        "aws_secretsmanager_secret.matrix_mas_synapse_shared_secret": {CREATE, UPDATE},
        "aws_security_group.matrix_mas[0]": {CREATE, UPDATE},
        "aws_security_group.matrix_mas_postgres[0]": {CREATE, UPDATE},
        "aws_security_group_rule.matrix_mas_dns_tcp[0]": {CREATE},
        "aws_security_group_rule.matrix_mas_dns_udp[0]": {CREATE},
        "aws_security_group_rule.matrix_mas_health_from_alb[0]": {CREATE},
        "aws_security_group_rule.matrix_mas_https_control_plane[0]": {CREATE},
        "aws_security_group_rule.matrix_mas_private_postgres[0]": {CREATE},
        "aws_security_group_rule.matrix_mas_web_from_alb[0]": {CREATE},
        "aws_service_discovery_service.matrix_mas[0]": {CREATE, UPDATE},
    },
    "public-edge": {
        "aws_acm_certificate_validation.matrix_mas[0]": {CREATE, UPDATE},
        "aws_cloudwatch_metric_alarm.matrix_mas_5xx[0]": {CREATE, UPDATE},
        "aws_cloudwatch_metric_alarm.matrix_mas_cpu[0]": {CREATE, UPDATE},
        "aws_cloudwatch_metric_alarm.matrix_mas_healthy_hosts[0]": {CREATE, UPDATE},
        "aws_ecs_service.matrix_mas[0]": {CREATE, UPDATE},
        "aws_lb_listener_certificate.matrix_mas_https[0]": {CREATE, UPDATE},
        "aws_lb_listener_rule.matrix_mas_auth_host[0]": {CREATE, UPDATE},
        "aws_route53_record.matrix_mas[0]": {CREATE, UPDATE},
        "aws_route53_record.matrix_mas_cert_validation*": {CREATE, UPDATE},
        "aws_route53_record.matrix_mas_ipv6[0]": {CREATE, UPDATE},
        "aws_security_group.alb": {UPDATE},
    },
    "migration": {
        "aws_ecs_task_definition.matrix_mas_migration[0]": {CREATE},
        "aws_security_group.matrix_synapse_efs[0]": {UPDATE},
        "aws_security_group.matrix_synapse_postgres[0]": {UPDATE},
        "aws_security_group_rule.matrix_mas_to_synapse_efs[0]": {CREATE},
    },
    "service-start": {
        "aws_cloudwatch_metric_alarm.matrix_mas_5xx[0]": {UPDATE},
        "aws_cloudwatch_metric_alarm.matrix_mas_cpu[0]": {UPDATE},
        "aws_cloudwatch_metric_alarm.matrix_mas_healthy_hosts[0]": {UPDATE},
        "aws_ecs_service.matrix_mas[0]": {UPDATE},
        "aws_ecs_task_definition.matrix_mas[0]": {REPLACE},
        "aws_ecs_task_definition.matrix_mas_migration[0]": {REPLACE},
    },
    "cutover": {
        "aws_ecs_service.matrix_mas[0]": {UPDATE},
        "aws_ecs_service.matrix_synapse[0]": {UPDATE},
        "aws_ecs_task_definition.matrix_mas[0]": {REPLACE},
        "aws_ecs_task_definition.matrix_synapse[0]": {CREATE, REPLACE},
        "aws_iam_role_policy.execution_secrets": {UPDATE},
        "aws_lb_listener_rule.matrix_mas_compat[0]": {CREATE, UPDATE},
        "aws_security_group.matrix": {UPDATE},
        "aws_security_group_rule.matrix_mas_from_synapse[0]": {CREATE},
    },
}


def action_allowed(phase: str, address: str, actions: list[str]) -> bool:
    action = tuple(actions)
    return any(
        (address == pattern or (pattern.endswith("*") and address.startswith(pattern[:-1])))
        and action in allowed_actions
        for pattern, allowed_actions in PHASE_RULES[phase].items()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=sorted(PHASE_RULES), required=True)
    parser.add_argument("plan_json", type=Path)
    args = parser.parse_args()

    plan = json.loads(args.plan_json.read_text(encoding="utf-8"))
    rejected: list[tuple[str, list[str]]] = []

    for resource in plan.get("resource_changes", []):
        address = resource.get("address", "")
        actions = resource.get("change", {}).get("actions", [])
        if actions in ([], ["no-op"], ["read"]):
            continue
        if not action_allowed(args.phase, address, actions):
            rejected.append((address, actions))

    if rejected:
        print(f"MAS {args.phase} plan rejected; address or action is outside the phase policy:")
        for address, actions in rejected:
            print(f"- {address}: {','.join(actions)}")
        return 1

    print(f"MAS {args.phase} plan accepted by exact address/action policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())