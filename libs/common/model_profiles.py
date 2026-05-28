from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
import yaml


class ModelProfileResolutionError(ValueError):
    """Raised when an effective agent model profile cannot be resolved."""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _safe_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_secret_like_updates(updates: dict[str, Any]) -> None:
    serialized = json.dumps(updates, sort_keys=True)
    if any(marker in serialized for marker in ("OPENAI_API_KEY", "Bearer ", "sk-")):
        raise ModelProfileResolutionError("model profile updates must not contain raw secret material")


def load_model_profile_contract(path: Path | str, overrides_path: Path | str | None = None) -> dict[str, Any]:
    profile_path = Path(path)
    if not profile_path.exists():
        raise ModelProfileResolutionError(f"model profile contract not found: {profile_path}")
    data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ModelProfileResolutionError("model profile contract must be a mapping")
    if overrides_path:
        override_path = Path(overrides_path)
        if override_path.exists():
            overrides = yaml.safe_load(override_path.read_text(encoding="utf-8"))
            if not isinstance(overrides, dict):
                raise ModelProfileResolutionError("model profile overrides must be a mapping")
            data = _deep_merge(data, overrides)
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


def _load_overrides(path: Path | str) -> dict[str, Any]:
    override_path = Path(path)
    if not override_path.exists():
        return {}
    data = yaml.safe_load(override_path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ModelProfileResolutionError("model profile overrides must be a mapping")
    return data


def update_model_profile_binding(
    *,
    contract_path: Path | str,
    overrides_path: Path | str,
    audit_path: Path | str,
    agent: str,
    profile: str,
    deployment_profile: str,
    updates: dict[str, Any],
    actor: str,
    connectivity_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    allowed = {
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
    unknown = sorted(set(updates) - allowed)
    if unknown:
        raise ModelProfileResolutionError(f"unsupported model profile update keys: {', '.join(unknown)}")
    _reject_secret_like_updates(updates)

    old_contract = load_model_profile_contract(contract_path, overrides_path)
    old_effective = resolve_effective_model_profile(
        old_contract,
        agent=agent,
        profile=profile,
        deployment_profile=deployment_profile,
    )

    overrides = _load_overrides(overrides_path)
    binding_path = overrides.setdefault("agents", {}).setdefault(agent, {}).setdefault("profiles", {}).setdefault(profile, {}).setdefault("bindings", {}).setdefault(deployment_profile, {})
    if not isinstance(binding_path, dict):
        raise ModelProfileResolutionError("model profile override binding must be a mapping")
    binding_path.update(copy.deepcopy(updates))

    override_path = Path(overrides_path)
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(yaml.safe_dump(overrides, sort_keys=True), encoding="utf-8")

    new_contract = load_model_profile_contract(contract_path, overrides_path)
    new_effective = resolve_effective_model_profile(
        new_contract,
        agent=agent,
        profile=profile,
        deployment_profile=deployment_profile,
    )

    safe_connectivity = connectivity_result or {}
    _reject_secret_like_updates(safe_connectivity)
    record = {
        "actor": actor or "unknown",
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "profile": profile,
        "deployment_profile": deployment_profile,
        "old_effective_config_hash": _safe_hash(old_effective),
        "new_effective_config_hash": _safe_hash(new_effective),
        "connectivity_check_result": safe_connectivity,
        "secrets_printed": False,
    }
    audit = Path(audit_path)
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")

    return {
        "agent": agent,
        "profile": profile,
        "deployment_profile": deployment_profile,
        "effective": new_effective,
        "audit": record,
        "secrets_printed": False,
    }


def _base_url_join(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


async def _httpx_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(url, json=payload, headers=headers, timeout=timeout)


async def check_model_profile_connectivity(
    effective: dict[str, Any],
    *,
    post_json: Callable[[str, dict[str, Any], dict[str, str], float], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Run a minimal OpenAI-compatible chat probe and return redacted status only."""
    endpoint = _as_dict(effective.get("endpoint"))
    base_url = str(endpoint.get("base_url") or "")
    if not base_url:
        raise ModelProfileResolutionError("effective model profile has no endpoint base_url")
    model = str(effective.get("model") or "")
    if not model:
        raise ModelProfileResolutionError("effective model profile has no model")

    timeout = float(effective.get("timeout_seconds") or 30)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "health check"}],
        # llama.cpp can abort on one-token probes with prompt-cache slot reuse on
        # the production Qwen server. Keep the probe tiny but ask for enough
        # tokens to exercise the endpoint without hitting that server edge case.
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    }
    headers: dict[str, str] = {}
    post = post_json or _httpx_post_json
    start = time.monotonic()
    try:
        response = await post(
            _base_url_join(base_url, "chat/completions"),
            payload,
            headers,
            timeout,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        ok = 200 <= status_code < 300
        detail = "ok" if ok else "model provider returned non-success status"
    except (httpx.RequestError, TimeoutError, OSError) as exc:
        status_code = None
        ok = False
        detail = exc.__class__.__name__
    latency_ms = int((time.monotonic() - start) * 1000)
    return {
        "ok": ok,
        "agent": effective.get("agent", ""),
        "profile": effective.get("profile", ""),
        "deployment_profile": effective.get("deployment_profile", ""),
        "provider": effective.get("provider", ""),
        "endpoint_ref": effective.get("endpoint_ref", ""),
        "model": model,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "detail": detail,
        "secrets_printed": False,
    }
