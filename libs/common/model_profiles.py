from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ModelProfileResolutionError(ValueError):
    """Raised when an effective agent model profile cannot be resolved."""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def load_model_profile_contract(path: Path | str) -> dict[str, Any]:
    profile_path = Path(path)
    if not profile_path.exists():
        raise ModelProfileResolutionError(f"model profile contract not found: {profile_path}")
    data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ModelProfileResolutionError("model profile contract must be a mapping")
    return data


def resolve_effective_model_profile(
    contract: dict[str, Any],
    *,
    agent: str,
    profile: str,
    deployment_profile: str,
) -> dict[str, Any]:
    agents = _as_dict(contract.get("agents"))
    agent_data = _as_dict(agents.get(agent))
    if not agent_data:
        raise ModelProfileResolutionError(f"unknown agent: {agent}")

    profiles = _as_dict(agent_data.get("profiles"))
    profile_data = _as_dict(profiles.get(profile))
    if not profile_data:
        raise ModelProfileResolutionError(f"unknown profile for {agent}: {profile}")

    bindings = _as_dict(profile_data.get("bindings"))
    binding = _as_dict(bindings.get(deployment_profile))
    if not binding:
        raise ModelProfileResolutionError(
            f"unknown deployment profile for {agent}.{profile}: {deployment_profile}"
        )

    providers = _as_dict(contract.get("providers"))
    provider_name = str(binding.get("provider") or "")
    provider = _as_dict(providers.get(provider_name))
    if not provider:
        raise ModelProfileResolutionError(f"unknown provider: {provider_name}")

    endpoints = _as_dict(contract.get("endpoints"))
    endpoint_ref = str(binding.get("endpoint_ref") or "")
    endpoint = _as_dict(endpoints.get(endpoint_ref))
    if not endpoint:
        raise ModelProfileResolutionError(f"unknown endpoint_ref: {endpoint_ref}")
    if endpoint.get("provider") != provider_name:
        raise ModelProfileResolutionError(
            f"endpoint provider mismatch for {endpoint_ref}: {endpoint.get('provider')} != {provider_name}"
        )

    secret_refs = _as_dict(contract.get("secret_refs"))
    secret_ref = str(binding.get("secret_ref") or "")
    secret = _as_dict(secret_refs.get(secret_ref))
    if not secret:
        raise ModelProfileResolutionError(f"unknown secret_ref: {secret_ref}")

    bootstrap_env = _as_dict(binding.get("bootstrap_env"))
    safe_bootstrap_env = {
        key: value
        for key, value in bootstrap_env.items()
        if "KEY" not in key.upper()
        and "TOKEN" not in key.upper()
        and "SECRET" not in key.upper()
        and "PASSWORD" not in key.upper()
    }

    return {
        "agent": agent,
        "profile": profile,
        "deployment_profile": deployment_profile,
        "purpose": profile_data.get("purpose", ""),
        "runtime_surface": profile_data.get("runtime_surface", ""),
        "capability_expectations": list(profile_data.get("capability_expectations", []) or []),
        "provider": provider_name,
        "provider_kind": provider.get("kind", ""),
        "endpoint_ref": endpoint_ref,
        "endpoint": {
            "base_url": endpoint.get("base_url", ""),
            "visibility": endpoint.get("visibility", ""),
            "auth": endpoint.get("auth", ""),
        },
        "model": binding.get("model", ""),
        "temperature": binding.get("temperature"),
        "max_tokens": binding.get("max_tokens"),
        "timeout_seconds": binding.get("timeout_seconds"),
        "cost_tier": binding.get("cost_tier", ""),
        "latency_tier": binding.get("latency_tier", ""),
        "fallback_profile": binding.get("fallback_profile", "none"),
        "secret": {
            "ref": secret_ref,
            "configured": secret_ref != "none",
            "display": secret.get("display", "configured by secret handle"),
        },
        "bootstrap_env": safe_bootstrap_env,
        "secrets_printed": False,
    }
