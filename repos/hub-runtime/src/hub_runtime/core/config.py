from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    """Environment-driven runtime configuration."""

    loop_type: str = "hermes_worker"
    inference_provider: str = "litellm"
    inference_base_url: str = "http://localhost:4000"
    inference_api_key: str | None = None
    inference_model: str = "NousResearch/Hermes-3-Llama-3.1-8B"
    max_iterations: int = 8
    request_timeout_seconds: float = 120.0
    temperature: float = 0.2
    max_tokens: int = 2048

    @classmethod
    def from_env(cls, env_vars: Mapping[str, str]) -> "RuntimeConfig":
        provider = _read_provider(env_vars)
        return cls(
            loop_type=env_vars.get("LOOP_TYPE", "hermes_worker"),
            inference_provider=provider,
            inference_base_url=_read_inference_base_url(env_vars, provider),
            inference_api_key=_read_inference_api_key(env_vars, provider),
            inference_model=_read_inference_model(env_vars, provider),
            max_iterations=_read_int(env_vars, "HUB_MAX_ITERATIONS", default=8, minimum=1),
            request_timeout_seconds=_read_float(
                env_vars,
                "HUB_REQUEST_TIMEOUT_SECONDS",
                default=120.0,
                exclusive_minimum=0.0,
            ),
            temperature=_read_float(env_vars, "HUB_TEMPERATURE", default=0.2, minimum=0.0),
            max_tokens=_read_int(env_vars, "HUB_MAX_TOKENS", default=2048, minimum=1),
        )

    @property
    def chat_completions_url(self) -> str:
        base_url = self.inference_base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    @property
    def litellm_base_url(self) -> str:
        """Backward-compatible alias for older runtime callers."""

        return self.inference_base_url

    @property
    def litellm_api_key(self) -> str | None:
        """Backward-compatible alias for older runtime callers."""

        return self.inference_api_key

    @property
    def hermes_model(self) -> str:
        """Backward-compatible alias for older runtime callers."""

        return self.inference_model


def _read_provider(env_vars: Mapping[str, str]) -> str:
    provider = (
        env_vars.get("INFERENCE_PROVIDER")
        or env_vars.get("MODEL_PROVIDER")
        or ("zenith" if _has_zenith_env(env_vars) else "litellm")
    )
    return provider.strip().lower().replace("_", "-")


def _has_zenith_env(env_vars: Mapping[str, str]) -> bool:
    return any(
        env_vars.get(name)
        for name in (
            "ZENITH_OPENAI_BASE_URL",
            "ZENITH_API_KEY",
            "ZENITH_STATE_FILE",
            "RUNPOD_ENDPOINT_API_KEY",
        )
    )


def _read_inference_base_url(env_vars: Mapping[str, str], provider: str) -> str:
    base_url = (
        env_vars.get("INFERENCE_BASE_URL")
        or env_vars.get("OPENAI_BASE_URL")
        or env_vars.get("LITELLM_BASE_URL")
    )
    if provider == "zenith":
        base_url = (
            env_vars.get("ZENITH_OPENAI_BASE_URL")
            or base_url
            or _read_zenith_state_url(env_vars)
        )
        if not base_url:
            raise ValueError(
                "ZENITH_OPENAI_BASE_URL, OPENAI_BASE_URL, or ZENITH_STATE_FILE is required "
                "when INFERENCE_PROVIDER=zenith"
            )
    return (base_url or "http://localhost:4000").rstrip("/")


def _read_zenith_state_url(env_vars: Mapping[str, str]) -> str | None:
    state_file = env_vars.get("ZENITH_STATE_FILE")
    if not state_file:
        return None
    try:
        payload = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read ZENITH_STATE_FILE: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"ZENITH_STATE_FILE is not valid JSON: {exc}") from exc
    value = payload.get("openai_base_url")
    return str(value).strip() if value else None


def _read_inference_api_key(env_vars: Mapping[str, str], provider: str) -> str | None:
    if provider == "zenith":
        return (
            env_vars.get("INFERENCE_API_KEY")
            or env_vars.get("ZENITH_API_KEY")
            or env_vars.get("RUNPOD_ENDPOINT_API_KEY")
            or env_vars.get("RUNPOD_API_KEY")
            or env_vars.get("OPENAI_API_KEY")
            or env_vars.get("LITELLM_API_KEY")
            or None
        )
    return (
        env_vars.get("INFERENCE_API_KEY")
        or env_vars.get("OPENAI_API_KEY")
        or env_vars.get("LITELLM_API_KEY")
        or None
    )


def _read_inference_model(env_vars: Mapping[str, str], provider: str) -> str:
    model = env_vars.get("INFERENCE_MODEL") or env_vars.get("OPENAI_MODEL")
    if provider == "zenith":
        return (
            model
            or env_vars.get("ZENITH_MODEL")
            or env_vars.get("HERMES_MODEL")
            or "gpt-oss-120b"
        )
    return model or env_vars.get("HERMES_MODEL") or "NousResearch/Hermes-3-Llama-3.1-8B"


def _read_int(
    env_vars: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int | None = None,
) -> int:
    value = int(env_vars.get(name, str(default)))
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _read_float(
    env_vars: Mapping[str, str],
    name: str,
    *,
    default: float,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    value = float(env_vars.get(name, str(default)))
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if exclusive_minimum is not None and value <= exclusive_minimum:
        raise ValueError(f"{name} must be > {exclusive_minimum}")
    return value
