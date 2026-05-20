#!/usr/bin/env python3
"""Validate Hub/Hermes model profile contracts."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "infra/model-profiles.yaml"
DOC_PATH = ROOT / "infra/MODEL_PROFILES.md"

REQUIRED_DEPLOYMENT_PROFILES = {
    "local-dev",
    "self-hosted-single-node",
    "cloud-aws-staging",
    "cloud-aws-prod",
}
REQUIRED_BINDING_KEYS = {
    "provider",
    "endpoint_ref",
    "model",
    "secret_ref",
    "temperature",
    "max_tokens",
    "timeout_seconds",
    "cost_tier",
    "latency_tier",
    "fallback_profile",
}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{12,}"),
]


def check(name: str, ok: bool, detail: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail, "data": data or {}}


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def binding_tests(data: dict[str, Any]) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    agents = as_dict(data.get("agents"))
    providers = as_dict(data.get("providers"))
    endpoints = as_dict(data.get("endpoints"))
    secret_refs = as_dict(data.get("secret_refs"))

    for agent_name, agent in agents.items():
        profiles = as_dict(as_dict(agent).get("profiles"))
        for profile_name, profile in profiles.items():
            profile = as_dict(profile)
            tests.append(check(
                f"profiles.{agent_name}.{profile_name}.purpose",
                bool(str(profile.get("purpose", "")).strip()),
                "profile purpose is documented",
            ))
            tests.append(check(
                f"profiles.{agent_name}.{profile_name}.capabilities",
                bool(profile.get("capability_expectations")),
                "profile capability expectations are documented",
            ))
            bindings = as_dict(profile.get("bindings"))
            tests.append(check(
                f"profiles.{agent_name}.{profile_name}.bindings_nonempty",
                bool(bindings),
                "profile has at least one deployment binding",
            ))
            for deployment_profile, binding in bindings.items():
                binding = as_dict(binding)
                missing = sorted(key for key in REQUIRED_BINDING_KEYS if key not in binding or binding.get(key) in (None, ""))
                provider = binding.get("provider")
                endpoint_ref = binding.get("endpoint_ref")
                endpoint = as_dict(endpoints.get(str(endpoint_ref)))
                secret_ref = binding.get("secret_ref")
                tests.append(check(
                    f"bindings.{agent_name}.{profile_name}.{deployment_profile}.required_keys",
                    not missing,
                    "binding has required provider/model/runtime/fallback keys",
                    {"missing": missing},
                ))
                tests.append(check(
                    f"bindings.{agent_name}.{profile_name}.{deployment_profile}.known_provider",
                    provider in providers,
                    "binding provider exists in providers map",
                    {"provider": provider},
                ))
                tests.append(check(
                    f"bindings.{agent_name}.{profile_name}.{deployment_profile}.known_endpoint",
                    endpoint_ref in endpoints,
                    "binding endpoint_ref exists in endpoints map",
                    {"endpoint_ref": endpoint_ref},
                ))
                tests.append(check(
                    f"bindings.{agent_name}.{profile_name}.{deployment_profile}.endpoint_provider_matches",
                    not endpoint or endpoint.get("provider") == provider,
                    "endpoint provider matches binding provider",
                    {"endpoint_provider": endpoint.get("provider"), "binding_provider": provider},
                ))
                tests.append(check(
                    f"bindings.{agent_name}.{profile_name}.{deployment_profile}.known_secret_ref",
                    secret_ref in secret_refs,
                    "binding secret_ref is a known safe handle",
                    {"secret_ref": secret_ref},
                ))
                tests.append(check(
                    f"bindings.{agent_name}.{profile_name}.{deployment_profile}.no_raw_secret_ref",
                    secret_ref in {"none", "hub-secret-handle"} and not str(secret_ref).startswith(("sk-", "Bearer ")),
                    "binding references a safe secret handle, not a raw secret",
                ))

    return tests


def scalar_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for child in value.values():
            values.extend(scalar_values(child))
        return values
    if isinstance(value, list):
        values: list[str] = []
        for child in value:
            values.extend(scalar_values(child))
        return values
    if isinstance(value, (str, int, float, bool)):
        return [str(value)]
    return []


def main() -> int:
    files = ["infra/model-profiles.yaml", "infra/MODEL_PROFILES.md", "scripts/model_profile_check.py"]
    tests: list[dict[str, Any]] = []

    tests.append(check("model_profiles.file_exists", PROFILE_PATH.exists(), "model profile YAML exists"))
    tests.append(check("model_profiles.doc_exists", DOC_PATH.exists(), "model profile docs exist"))
    if not PROFILE_PATH.exists():
        result = {
            "files": files,
            "tests": tests,
            "deploy": {"changed_production": False, "started_local_containers": False},
            "blocker": "model profile file missing",
            "next": "Create infra/model-profiles.yaml.",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    raw = PROFILE_PATH.read_text()
    data = yaml.safe_load(raw)
    data = as_dict(data)

    deployment_profiles = set(data.get("deployment_profiles", []) or [])
    tests.append(check(
        "model_profiles.deployment_profiles_cover_profiles_contract",
        REQUIRED_DEPLOYMENT_PROFILES.issubset(deployment_profiles),
        "model profile contract references all deployment profiles",
        {"missing": sorted(REQUIRED_DEPLOYMENT_PROFILES - deployment_profiles)},
    ))

    tests.append(check("model_profiles.providers_nonempty", bool(as_dict(data.get("providers"))), "providers map is non-empty"))
    tests.append(check("model_profiles.endpoints_nonempty", bool(as_dict(data.get("endpoints"))), "endpoints map is non-empty"))
    tests.append(check("model_profiles.agents_nonempty", bool(as_dict(data.get("agents"))), "agents map is non-empty"))
    tests.append(check("model_profiles.secret_refs_nonempty", bool(as_dict(data.get("secret_refs"))), "secret handles are declared"))

    tests.extend(binding_tests(data))

    frank = as_dict(as_dict(data.get("agents")).get("frank"))
    frank_profiles = as_dict(frank.get("profiles"))
    prod_review = as_dict(as_dict(as_dict(frank_profiles.get("review_brief_compiler")).get("bindings")).get("cloud-aws-prod"))
    tests.append(check(
        "model_profiles.frank_prod_review_internal_qwen",
        prod_review.get("provider") == "hub-internal-openai-compatible"
        and prod_review.get("endpoint_ref") == "prod-llama-server"
        and prod_review.get("model") == "Qwen3.5-9B-Q4_K_M.gguf"
        and prod_review.get("secret_ref") == "none",
        "Frank prod review profile points to internal Qwen llama-server without a real secret",
    ))

    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS for value in scalar_values(data) if pattern.search(value)]
    tests.append(check(
        "model_profiles.no_raw_secret_patterns",
        not secret_hits,
        "model profile YAML does not contain raw secret-looking values",
        {"patterns": secret_hits},
    ))

    surface = as_dict(data.get("zenithos_operator_surface"))
    tests.append(check(
        "model_profiles.zenithos_surface_declared",
        bool(surface.get("route_shape")) and bool(surface.get("must_show")) and bool(surface.get("must_not_show")),
        "ZenithOS operator surface expectations are declared",
    ))

    ok = all(test["ok"] for test in tests)
    result = {
        "files": files,
        "tests": tests,
        "deploy": {"changed_production": False, "started_local_containers": False},
        "blocker": "none" if ok else "model profile validation failed",
        "next": "Wire Hub effective model-profile resolution and ZenithOS operator APIs after this static contract is reviewed.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
