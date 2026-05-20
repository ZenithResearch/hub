#!/usr/bin/env python3
"""Validate Hub deployment profile contracts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "infra/deployment-profiles.yaml"
REQUIRED_PROFILES = [
    "local-dev",
    "self-hosted-single-node",
    "cloud-aws-staging",
    "cloud-aws-prod",
]
REQUIRED_SECTIONS = [
    "purpose",
    "source_of_truth",
    "services",
    "env_contract",
    "matrix",
    "model_serving",
    "backup_restore",
    "smoke",
    "cd_policy",
]
SOURCE_KEYS = ["code_config", "review_auth", "cases_runs", "artifacts", "matrix", "model_artifacts"]


def check(name: str, ok: bool, detail: str, data: dict | None = None) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "data": data or {}}


def main() -> int:
    tests: list[dict] = []
    if not PROFILE_PATH.exists():
        result = {
            "files": [str(PROFILE_PATH.relative_to(ROOT))],
            "tests": [check("profiles.file_exists", False, "profile file missing")],
            "deploy": {"changed_production": False},
            "blocker": "deployment profile file missing",
            "next": "Create infra/deployment-profiles.yaml.",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    data = yaml.safe_load(PROFILE_PATH.read_text())
    profiles = data.get("profiles", {}) if isinstance(data, dict) else {}
    tests.append(check("profiles.required_names", all(p in profiles for p in REQUIRED_PROFILES), "all required profiles present", {"profiles": sorted(profiles)}))

    for profile_name in REQUIRED_PROFILES:
        profile = profiles.get(profile_name, {})
        tests.append(check(
            f"profiles.{profile_name}.required_sections",
            all(section in profile for section in REQUIRED_SECTIONS),
            "all required sections present",
            {"missing": [section for section in REQUIRED_SECTIONS if section not in profile]},
        ))
        source = profile.get("source_of_truth", {})
        tests.append(check(
            f"profiles.{profile_name}.source_of_truth_complete",
            all(key in source and str(source.get(key)).strip() for key in SOURCE_KEYS),
            "all data classes have a source of truth",
            {"missing": [key for key in SOURCE_KEYS if not str(source.get(key, "")).strip()]},
        ))
        services = profile.get("services", {})
        tests.append(check(
            f"profiles.{profile_name}.required_services_nonempty",
            bool(services.get("required")),
            "required service list is non-empty",
        ))
        smoke = profile.get("smoke", {})
        tests.append(check(
            f"profiles.{profile_name}.smoke_static_and_runtime",
            bool(smoke.get("static")) and bool(smoke.get("runtime")),
            "static and runtime smoke commands are documented",
        ))
        tests.append(check(
            f"profiles.{profile_name}.backup_restore_policy",
            bool(profile.get("backup_restore", {}).get("required_before_reset")) and "durable" in profile.get("backup_restore", {}),
            "backup/reset policy and durability flag are documented",
        ))

    prod = profiles.get("cloud-aws-prod", {})
    prod_required = set(prod.get("services", {}).get("required", []))
    prod_expected = {"gateway-http", "queue", "eventbus", "cases", "frank", "stt-http", "llama-server"}
    tests.append(check(
        "profiles.cloud-aws-prod.core_services",
        prod_expected.issubset(prod_required),
        "prod includes current core ECS services",
        {"missing": sorted(prod_expected - prod_required)},
    ))
    tests.append(check(
        "profiles.cloud-aws-prod.manual_cd",
        "manual" in str(prod.get("cd_policy", "")).lower() and "OIDC".lower() in str(prod.get("cd_policy", "")).lower(),
        "prod CD policy keeps manual OIDC approval path",
    ))

    matrix_doc = (ROOT / "infra/matrix/DEPLOYMENT_PARITY.md").exists()
    tests.append(check("profiles.matrix_parity_doc_exists", matrix_doc, "Matrix parity doc exists"))

    ok = all(test["ok"] for test in tests)
    result = {
        "files": ["infra/deployment-profiles.yaml", "infra/DEPLOYMENT_PROFILES.md", "scripts/deployment_profile_check.py"],
        "tests": tests,
        "deploy": {"changed_production": False, "started_local_containers": False},
        "blocker": "none" if ok else "deployment profile validation failed",
        "next": "Move to Project H: ZenithOS/Hermes-native model/profile configuration, or wire profile checks into CI.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
