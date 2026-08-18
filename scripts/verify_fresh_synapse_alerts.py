#!/usr/bin/env python3
"""Verify cost and expiry alerts without printing the subscriber endpoint."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

EXPECTED_PROFILE = "zenith-hypha-synapse"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_ROLE_ARN_FRAGMENT = "assumed-role/HyphaSynapseDeploymentRole/"
EXPECTED_BUDGET = "hypha-synapse-monthly"
EXPECTED_TOPIC_ARN = "arn:aws:sns:us-east-1:610992396917:hypha-synapse-expiry-alerts"
EXPECTED_SCHEDULES = {
    "hypha-synapse-expiry-60-days": "at(2026-12-19T20:08:42)",
    "hypha-synapse-expiry-30-days": "at(2027-01-18T20:08:42)",
    "hypha-synapse-expiry-14-days": "at(2027-02-03T20:08:42)",
    "hypha-synapse-expiry-7-days": "at(2027-02-10T20:08:42)",
}


class VerificationError(RuntimeError):
    """A safe verification failure."""


def _aws_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_STS",
        "AWS_ENDPOINT_URL_BUDGETS",
        "AWS_ENDPOINT_URL_SNS",
        "AWS_ENDPOINT_URL_SCHEDULER",
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
        *arguments,
    ]
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
        raise VerificationError("AWS CLI execution failed") from exc
    if process.returncode != 0:
        raise VerificationError("AWS alert verification command failed")
    return process.stdout


def _object(raw: str, description: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VerificationError("AWS returned invalid JSON for " + description) from exc
    if not isinstance(value, dict):
        raise VerificationError("AWS returned invalid JSON for " + description)
    return value


def _verify_role() -> None:
    identity = _object(
        _run_aws(("sts", "get-caller-identity", "--output", "json")),
        "caller identity",
    )
    arn = identity.get("Arn")
    if identity.get("Account") != EXPECTED_ACCOUNT or not isinstance(arn, str):
        raise VerificationError("refusing alert verification: wrong AWS account")
    if EXPECTED_ROLE_ARN_FRAGMENT not in arn:
        raise VerificationError("refusing alert verification: deployment role is required")


def _verify_budget() -> None:
    response = _object(
        _run_aws(
            (
                "budgets",
                "describe-budget",
                "--account-id",
                EXPECTED_ACCOUNT,
                "--budget-name",
                EXPECTED_BUDGET,
                "--output",
                "json",
            )
        ),
        "budget",
    )
    budget = response.get("Budget")
    if not isinstance(budget, dict) or budget.get("BudgetName") != EXPECTED_BUDGET:
        raise VerificationError("monthly budget identity mismatch")
    limit = budget.get("BudgetLimit")
    try:
        amount = Decimal(limit.get("Amount", "")) if isinstance(limit, dict) else None
    except (InvalidOperation, TypeError):
        amount = None
    if amount != Decimal("30") or not isinstance(limit, dict) or limit.get("Unit") != "USD":
        raise VerificationError("monthly budget limit mismatch")
    if budget.get("BudgetType") != "COST" or budget.get("TimeUnit") != "MONTHLY":
        raise VerificationError("monthly budget policy mismatch")


def _verify_subscription() -> None:
    response = _object(
        _run_aws(
            (
                "sns",
                "list-subscriptions-by-topic",
                "--topic-arn",
                EXPECTED_TOPIC_ARN,
                "--output",
                "json",
            )
        ),
        "expiry subscription",
    )
    subscriptions = response.get("Subscriptions")
    if not isinstance(subscriptions, list):
        raise VerificationError("expiry subscription response was invalid")
    confirmed = [
        item
        for item in subscriptions
        if isinstance(item, dict)
        and item.get("Protocol") == "email"
        and isinstance(item.get("SubscriptionArn"), str)
        and item.get("SubscriptionArn") != "PendingConfirmation"
    ]
    if len(confirmed) != 1:
        raise VerificationError("exactly one confirmed expiry email subscription is required")


def _verify_schedules() -> None:
    for name, expression in EXPECTED_SCHEDULES.items():
        schedule = _object(
            _run_aws(("scheduler", "get-schedule", "--name", name, "--output", "json")),
            "expiry schedule",
        )
        target = schedule.get("Target")
        if (
            schedule.get("Name") != name
            or schedule.get("State") != "ENABLED"
            or schedule.get("ScheduleExpression") != expression
            or schedule.get("ScheduleExpressionTimezone") != "UTC"
            or not isinstance(target, dict)
            or target.get("Arn") != EXPECTED_TOPIC_ARN
        ):
            raise VerificationError("expiry schedule mismatch")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing alert verification: exact profile and region are required", file=sys.stderr)
        return 2
    try:
        _verify_role()
        _verify_budget()
        _verify_subscription()
        _verify_schedules()
    except VerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"budget_verified": True, "expiry_subscription_confirmed": True, "schedules_verified": 4}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
