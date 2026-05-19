#!/usr/bin/env python3
"""Production readiness smoke checks for Hub.

The script is intentionally conservative:
- public mode uses only unauthenticated HTTP checks;
- operator mode reads admin credentials from environment/keychain but never prints them;
- internal mode checks ECS service state by default and can optionally run one-off
  in-VPC endpoint probes when --run-internal-probes is supplied.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_REGION = "us-east-1"
DEFAULT_PROFILE = "zenith-hermes"
DEFAULT_CLUSTER = "zenith-hub-prod-cluster"
DEFAULT_HUB_URL = "https://hub.zenith-research.ca"
DEFAULT_KEYCHAIN_SERVICE = "zenith-hub-review-access-admin-token"

PROD_SERVICES = [
    "zenith-hub-prod-gateway-http",
    "zenith-hub-prod-runtime-grpc",
    "zenith-hub-prod-tool-sandbox",
    "zenith-hub-prod-queue",
    "zenith-hub-prod-cases",
    "zenith-hub-prod-eventbus",
    "zenith-hub-prod-stt-http",
    "zenith-hub-prod-frank",
    "zenith-hub-prod-llama-server",
]

BEARER_RE = re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(review[_-]?access[_-]?admin[_-]?token|api[_-]?key|secret|password|token)"
    r"([\"'=:\s]*[=:][\"'=:\s]*)([^\s\"']+)"
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    data: dict[str, Any] | None = None


def redact_text(value: str) -> str:
    value = BEARER_RE.sub(lambda match: match.group(1) + "[REDACTED]", value)
    return SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if is_sensitive_key(key) else redact_obj(item)) for key, item in value.items()}
    return value


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in ("token", "secret", "password", "api_key", "apikey", "authorization"))


def http_json(method: str, url: str, *, token: str | None = None, timeout: float = 10.0) -> tuple[int, Any]:
    headers = {"accept": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - operator-provided URL only
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"body": body[:500]}
        return exc.code, payload


def run_command(args: list[str], *, timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    return proc.returncode, redact_text(proc.stdout), redact_text(proc.stderr)


def aws_args(profile: str, region: str) -> list[str]:
    args = ["aws", "--region", region]
    if profile:
        args.extend(["--profile", profile])
    return args


def load_admin_token() -> tuple[str | None, str]:
    env_token = os.environ.get("REVIEW_ACCESS_ADMIN_TOKEN", "").strip()
    if env_token:
        return env_token, "env:REVIEW_ACCESS_ADMIN_TOKEN"

    service = os.environ.get("REVIEW_ACCESS_ADMIN_TOKEN_KEYCHAIN_SERVICE", DEFAULT_KEYCHAIN_SERVICE)
    account = os.environ.get("REVIEW_ACCESS_ADMIN_TOKEN_KEYCHAIN_ACCOUNT", "admin")
    if sys.platform == "darwin" and shutil.which("security"):
        keychain_commands = [
            (["security", "find-generic-password", "-s", service, "-a", account, "-w"], f"keychain:{service}/{account}"),
            (["security", "find-generic-password", "-s", service, "-w"], f"keychain:{service}"),
        ]
        for command, source in keychain_commands:
            proc = subprocess.run(command, capture_output=True, text=True, check=False)
            token = proc.stdout.strip()
            if proc.returncode == 0 and token:
                return token, source
    return None, "missing"


def check_public_health(base_url: str) -> Check:
    url = base_url.rstrip("/") + "/health"
    try:
        status, payload = http_json("GET", url, timeout=10.0)
        ok = 200 <= status < 300
        return Check("public.health", ok, f"HTTP {status}", {"url": url, "payload": redact_obj(payload)})
    except Exception as exc:  # noqa: BLE001 - smoke script reports all failures
        return Check("public.health", False, type(exc).__name__, {"url": url})


def check_operator(base_url: str) -> list[Check]:
    token, source = load_admin_token()
    if not token:
        return [Check("operator.credentials", False, "admin token unavailable", {"source": source})]

    checks: list[Check] = [Check("operator.credentials", True, "admin token loaded", {"source": source, "printed": False})]
    endpoints = [
        ("operator.capabilities", "/v1/admin/review-auth/capabilities"),
        ("operator.cases", "/v1/admin/cases?limit=1"),
        ("operator.queue_peek", "/v1/admin/queues/workspace/peek?n=1"),
    ]
    for name, path in endpoints:
        url = base_url.rstrip("/") + path
        try:
            status, payload = http_json("GET", url, token=token, timeout=15.0)
            ok = 200 <= status < 300
            data: dict[str, Any] = {"url": url, "status": status}
            if isinstance(payload, dict):
                if "items" in payload and isinstance(payload["items"], list):
                    data["items_count"] = len(payload["items"])
                elif "cases" in payload and isinstance(payload["cases"], list):
                    data["cases_count"] = len(payload["cases"])
                else:
                    data["payload_keys"] = sorted(payload.keys())[:12]
            checks.append(Check(name, ok, f"HTTP {status}", redact_obj(data)))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(name, False, type(exc).__name__, {"url": url}))
    return checks


def describe_services(profile: str, region: str, cluster: str) -> list[Check]:
    if not shutil.which("aws"):
        return [Check("internal.aws_cli", False, "aws CLI not found")]
    args = aws_args(profile, region) + [
        "ecs",
        "describe-services",
        "--cluster",
        cluster,
        "--services",
        *PROD_SERVICES,
        "--output",
        "json",
    ]
    code, out, err = run_command(args, timeout=90)
    if code != 0:
        return [Check("internal.ecs_services", False, "aws ecs describe-services failed", {"stderr": err[-500:]})]
    payload = json.loads(out)
    checks: list[Check] = []
    for svc in payload.get("services", []):
        name = svc.get("serviceName", "unknown")
        desired = svc.get("desiredCount")
        running = svc.get("runningCount")
        pending = svc.get("pendingCount")
        status = svc.get("status")
        task_def = str(svc.get("taskDefinition", "")).rsplit("/", 1)[-1]
        deployments = svc.get("deployments") or []
        rollout_states = [d.get("rolloutState") for d in deployments if d.get("rolloutState")]
        ok = status == "ACTIVE" and desired == running and pending == 0 and all(state == "COMPLETED" for state in rollout_states[:1])
        checks.append(
            Check(
                f"internal.ecs.{name}",
                ok,
                f"{status} desired={desired} running={running} pending={pending}",
                {"task_definition": task_def, "rollout_states": rollout_states[:3]},
            )
        )
    failures = payload.get("failures") or []
    for failure in failures:
        checks.append(Check("internal.ecs.failure", False, "service describe failure", redact_obj(failure)))
    return checks


def infer_run_task_config(profile: str, region: str, cluster: str, service_name: str) -> tuple[dict[str, Any] | None, str | None]:
    args = aws_args(profile, region) + [
        "ecs",
        "describe-services",
        "--cluster",
        cluster,
        "--services",
        service_name,
        "--output",
        "json",
    ]
    code, out, err = run_command(args, timeout=60)
    if code != 0:
        return None, err[-500:]
    services = json.loads(out).get("services") or []
    if not services:
        return None, f"service not found: {service_name}"
    svc = services[0]
    network = svc.get("networkConfiguration", {}).get("awsvpcConfiguration")
    task_def = svc.get("taskDefinition")
    if not network or not task_def:
        return None, "missing networkConfiguration or taskDefinition"
    td_args = aws_args(profile, region) + ["ecs", "describe-task-definition", "--task-definition", task_def, "--output", "json"]
    code, td_out, err = run_command(td_args, timeout=60)
    if code != 0:
        return None, err[-500:]
    td = json.loads(td_out).get("taskDefinition") or {}
    containers = td.get("containerDefinitions") or []
    if not containers:
        return None, "task definition has no containers"
    container_name = containers[0]["name"]
    config = {
        "taskDefinition": task_def,
        "containerName": container_name,
        "networkConfiguration": {
            "awsvpcConfiguration": {
                "subnets": network.get("subnets", []),
                "securityGroups": network.get("securityGroups", []),
                "assignPublicIp": network.get("assignPublicIp", "DISABLED"),
            }
        },
    }
    return config, None


def run_internal_probe(profile: str, region: str, cluster: str) -> list[Check]:
    if not shutil.which("aws"):
        return [Check("internal.probe.aws_cli", False, "aws CLI not found")]
    config, error = infer_run_task_config(profile, region, cluster, "zenith-hub-prod-frank")
    if not config:
        return [Check("internal.probe.config", False, error or "could not infer run-task config")]

    probe_code = r'''
import json, urllib.request, sys
checks=[]
def get(name, url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body=r.read().decode('utf-8','replace')[:500]
            checks.append({'name':name,'ok':200 <= r.status < 300,'status':r.status,'body':body})
    except Exception as e:
        checks.append({'name':name,'ok':False,'error':type(e).__name__})
get('llama.health','http://llama-server.zenith-hub-prod.local:3690/health')
get('stt.health','http://stt-http.zenith-hub-prod.local:8765/health')
print(json.dumps({'checks':checks}))
sys.exit(0 if all(c.get('ok') for c in checks) else 2)
'''.strip()
    overrides = {
        "containerOverrides": [
            {
                "name": config["containerName"],
                "command": ["python", "-c", probe_code],
            }
        ]
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(overrides, handle)
        overrides_path = handle.name
    try:
        args = aws_args(profile, region) + [
            "ecs",
            "run-task",
            "--cluster",
            cluster,
            "--launch-type",
            "FARGATE",
            "--task-definition",
            config["taskDefinition"],
            "--network-configuration",
            json.dumps(config["networkConfiguration"]),
            "--overrides",
            f"file://{overrides_path}",
            "--started-by",
            "prod-smoke-internal-probe",
            "--output",
            "json",
        ]
        code, out, err = run_command(args, timeout=90)
    finally:
        try:
            os.unlink(overrides_path)
        except OSError:
            pass
    if code != 0:
        return [Check("internal.probe.run_task", False, "ecs run-task failed", {"stderr": err[-500:]})]
    payload = json.loads(out)
    tasks = payload.get("tasks") or []
    failures = payload.get("failures") or []
    checks: list[Check] = []
    if failures:
        checks.append(Check("internal.probe.failure", False, "run-task failure", redact_obj(failures)))
    if not tasks:
        return checks or [Check("internal.probe.run_task", False, "run-task returned no task")]
    task_arn = tasks[0]["taskArn"]
    wait_args = aws_args(profile, region) + ["ecs", "wait", "tasks-stopped", "--cluster", cluster, "--tasks", task_arn]
    wait_code, _, wait_err = run_command(wait_args, timeout=240)
    if wait_code != 0:
        checks.append(Check("internal.probe.wait", False, "task did not stop cleanly", {"stderr": wait_err[-500:]}))
    desc_args = aws_args(profile, region) + ["ecs", "describe-tasks", "--cluster", cluster, "--tasks", task_arn, "--output", "json"]
    desc_code, desc_out, desc_err = run_command(desc_args, timeout=60)
    if desc_code != 0:
        checks.append(Check("internal.probe.describe", False, "describe task failed", {"stderr": desc_err[-500:]}))
        return checks
    desc = json.loads(desc_out)
    task = (desc.get("tasks") or [{}])[0]
    containers = task.get("containers") or []
    exit_code = containers[0].get("exitCode") if containers else None
    reason = containers[0].get("reason") if containers else None
    checks.append(
        Check(
            "internal.probe.task_exit",
            exit_code == 0,
            f"exit_code={exit_code}",
            {"task": task_arn.rsplit('/', 1)[-1], "reason": reason},
        )
    )
    return checks


def summarize(checks: list[Check], files: list[str], mode: str) -> dict[str, Any]:
    failed = [check for check in checks if not check.ok]
    deploy = {
        "cluster": DEFAULT_CLUSTER,
        "services_checked": [name for name in PROD_SERVICES if any(name in check.name for check in checks)] or [],
    }
    return {
        "files": files,
        "tests": [
            {"name": check.name, "ok": check.ok, "detail": check.detail, **({"data": check.data} if check.data is not None else {})}
            for check in checks
        ],
        "deploy": deploy,
        "blocker": "none" if not failed else "; ".join(f"{check.name}: {check.detail}" for check in failed[:5]),
        "next": next_action(mode, failed),
    }


def next_action(mode: str, failed: list[Check]) -> str:
    if failed:
        return "Fix or document the failing smoke check before treating prod as good to test."
    if mode == "public":
        return "Run operator and internal modes when credentials/AWS profile are available."
    if mode == "operator":
        return "Run internal mode to verify ECS service state and private endpoints."
    return "Proceed to Project B or the next requested checkpoint."


def main() -> int:
    parser = argparse.ArgumentParser(description="Hub production readiness smoke checks")
    parser.add_argument("--target", default="prod", choices=["prod", "local"], help="target environment label")
    parser.add_argument("--mode", default="public", choices=["public", "operator", "internal", "all"], help="checks to run")
    parser.add_argument("--hub-url", default=os.environ.get("HUB_URL", DEFAULT_HUB_URL))
    parser.add_argument("--aws-profile", default=os.environ.get("AWS_PROFILE", DEFAULT_PROFILE))
    parser.add_argument("--aws-region", default=os.environ.get("AWS_REGION", DEFAULT_REGION))
    parser.add_argument("--cluster", default=os.environ.get("ECS_CLUSTER", DEFAULT_CLUSTER))
    parser.add_argument("--run-internal-probes", action="store_true", help="run one-off ECS task to probe private endpoints")
    args = parser.parse_args()

    if args.target == "local" and args.hub_url == DEFAULT_HUB_URL:
        args.hub_url = "http://127.0.0.1:8080"

    checks: list[Check] = []
    if args.mode in {"public", "all"}:
        checks.append(check_public_health(args.hub_url))
    if args.mode in {"operator", "all"}:
        checks.extend(check_operator(args.hub_url))
    if args.mode in {"internal", "all"}:
        checks.extend(describe_services(args.aws_profile, args.aws_region, args.cluster))
        if args.run_internal_probes:
            checks.extend(run_internal_probe(args.aws_profile, args.aws_region, args.cluster))
        else:
            checks.append(Check("internal.private_endpoint_probe", True, "skipped; pass --run-internal-probes to run one-off ECS probe"))

    result = summarize(
        checks,
        files=["scripts/prod_smoke.py", "infra/aws_baseline_80/DEPLOYMENT.md"],
        mode=args.mode,
    )
    print(json.dumps(redact_obj(result), indent=2, sort_keys=True))
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
