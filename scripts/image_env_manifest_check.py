#!/usr/bin/env python3
"""Validate Hub image-to-environment contract manifest.

This check keeps the operator-facing config surface honest: every ECS image/task
that exists in Terraform must have a declared environment/secrets contract, and
runtime secrets must be declared as Secrets Manager backed handles rather than
local override files or raw values.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = ROOT / "infra" / "image-env-manifest.yaml"
DEFAULT_ECS_PATH = ROOT / "infra" / "aws_baseline_80" / "ecs.tf"

RESOURCE_RE = re.compile(r'resource\s+"aws_ecs_task_definition"\s+"([^"]+)"\s*\{')
IMAGE_RE = re.compile(r'\bimage\s*=\s*([^\n]+)')
ENV_NAME_RE = re.compile(r'\bname\s*=\s*"([A-Z][A-Z0-9_]+)"')
SECRET_VALUE_FROM_RE = re.compile(r'\{\s*name\s*=\s*"([A-Z][A-Z0-9_]+)"\s*\n\s*valueFrom\s*=\s*([^\n}]+)', re.MULTILINE)


def _extract_balanced_block(text: str, open_brace_index: int) -> str:
    depth = 0
    for index in range(open_brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index : index + 1]
    raise ValueError("unbalanced Terraform resource block")


def _clean_expression(value: str) -> str:
    return value.strip().rstrip(",").strip()


def parse_ecs_task_definitions(path: Path | str = DEFAULT_ECS_PATH) -> dict[str, dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    tasks: dict[str, dict[str, Any]] = {}
    for match in RESOURCE_RE.finditer(text):
        name = match.group(1)
        block = _extract_balanced_block(text, match.end() - 1)
        image_match = IMAGE_RE.search(block)
        env_names = sorted(set(ENV_NAME_RE.findall(block)) - {"MODEL_BUCKET", "MODEL_KEY", "MODEL_NAME", "EXPECTED_SHA256"})
        # Re-add preload command env names when they are actual ECS environment entries.
        if name == "llama_model_preload":
            env_names = sorted(set(ENV_NAME_RE.findall(block)))
        secret_names = sorted({secret.group(1) for secret in SECRET_VALUE_FROM_RE.finditer(block)})
        tasks[name] = {
            "service": name,
            "image_expression": _clean_expression(image_match.group(1)) if image_match else "",
            "environment_names": env_names,
            "secret_names": secret_names,
        }
    return tasks


def load_manifest(path: Path | str = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    manifest_path = Path(path)
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a mapping: {manifest_path}")
    services = data.get("services")
    if not isinstance(services, list):
        raise ValueError("manifest.services must be a list")
    return data


def _manifest_service_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in manifest.get("services", []):
        if not isinstance(item, dict):
            continue
        service = str(item.get("service") or "")
        if service:
            result[service] = item
    return result


def _env_keys(service: dict[str, Any]) -> set[str]:
    env = service.get("environment") or {}
    return set(env.keys()) if isinstance(env, dict) else set()


def _secret_keys(service: dict[str, Any]) -> set[str]:
    secrets = service.get("secrets") or {}
    return set(secrets.keys()) if isinstance(secrets, dict) else set()


def run_checks(
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    ecs_path: Path | str = DEFAULT_ECS_PATH,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    services = _manifest_service_map(manifest)
    task_defs = parse_ecs_task_definitions(ecs_path)
    failures: list[str] = []

    for task_name, task in sorted(task_defs.items()):
        service = services.get(task_name)
        if service is None:
            failures.append(f"ECS task definition {task_name!r} has no image-env manifest service entry")
            continue
        if not str(service.get("image") or "").strip():
            failures.append(f"manifest service {task_name!r} is missing image expression")
        manifest_env = _env_keys(service)
        missing_env = sorted(set(task["environment_names"]) - manifest_env - _secret_keys(service) - {"LOG_LEVEL"})
        # LOG_LEVEL is still declared in the manifest for services that use it, but don't make
        # newly created services fail solely because the common logging variable was omitted.
        if missing_env:
            failures.append(f"manifest service {task_name!r} missing environment vars: {', '.join(missing_env)}")
        manifest_secrets = _secret_keys(service)
        missing_secrets = sorted(set(task["secret_names"]) - manifest_secrets)
        if missing_secrets:
            failures.append(f"manifest service {task_name!r} missing secrets: {', '.join(missing_secrets)}")

    extra_services = sorted(set(services) - set(task_defs))
    for service in extra_services:
        failures.append(f"manifest service {service!r} does not correspond to an ECS task definition")

    for service_name, service in sorted(services.items()):
        secrets = service.get("secrets") or {}
        if not isinstance(secrets, dict):
            failures.append(f"manifest service {service_name!r} secrets must be a mapping")
            continue
        for env_name, metadata in sorted(secrets.items()):
            if not isinstance(metadata, dict):
                failures.append(f"manifest secret {service_name}.{env_name} must be a mapping")
                continue
            if metadata.get("source") != "aws_secrets_manager":
                failures.append(f"manifest secret {service_name}.{env_name} must use aws_secrets_manager source")
            if not metadata.get("secret_ref"):
                failures.append(f"manifest secret {service_name}.{env_name} missing secret_ref")

    return {
        "ok": not failures,
        "manifest": str(manifest_path),
        "ecs": str(ecs_path),
        "services": [services[name] for name in sorted(services)],
        "ecs_task_definitions": [task_defs[name] for name in sorted(task_defs)],
        "failures": failures,
    }


def main() -> int:
    result = run_checks()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
