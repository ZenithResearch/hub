import json
import os
from typing import Any

import boto3


ecs = boto3.client("ecs")


def _flatten(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for nested in value.values():
            out.extend(_flatten(nested))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for nested in value:
            out.extend(_flatten(nested))
        return out
    return [str(value)]


def _event_references_secret(event: dict[str, Any], target_secret_arn: str) -> bool:
    target_secret_name = target_secret_arn.split(":secret:", 1)[-1]
    candidates: list[str] = []
    candidates.extend(_flatten(event.get("resources")))
    detail = event.get("detail") or {}
    candidates.extend(_flatten(detail.get("requestParameters")))
    candidates.extend(_flatten(detail.get("responseElements")))
    candidates.extend(_flatten(detail.get("additionalEventData")))
    return any(
        candidate == target_secret_arn
        or candidate.startswith(f"{target_secret_arn}:")
        or candidate == target_secret_name
        for candidate in candidates
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    target_secret_arn = os.environ["TARGET_SECRET_ARN"]
    cluster_arn = os.environ["ECS_CLUSTER_ARN"]
    service_name = os.environ["ECS_SERVICE_NAME"]

    detail = event.get("detail") or {}
    event_name = detail.get("eventName")
    if event_name not in {"RotationSucceeded", "UpdateSecretVersionStage"}:
        print(json.dumps({"action": "ignored", "reason": "event_name", "eventName": event_name}))
        return {"restarted": False, "reason": "event_name"}

    if not _event_references_secret(event, target_secret_arn):
        print(json.dumps({"action": "ignored", "reason": "different_secret", "eventName": event_name}))
        return {"restarted": False, "reason": "different_secret"}

    response = ecs.update_service(
        cluster=cluster_arn,
        service=service_name,
        forceNewDeployment=True,
    )
    service = response["service"]
    result = {
        "restarted": True,
        "eventName": event_name,
        "clusterArn": cluster_arn,
        "serviceName": service_name,
        "taskDefinition": service.get("taskDefinition"),
        "desiredCount": service.get("desiredCount"),
        "runningCount": service.get("runningCount"),
        "pendingCount": service.get("pendingCount"),
    }
    print(json.dumps(result, sort_keys=True))
    return result
