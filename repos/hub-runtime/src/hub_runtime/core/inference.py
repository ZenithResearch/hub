from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from hub_runtime.core.config import RuntimeConfig

ChatMessage = Mapping[str, Any]


@dataclass(frozen=True)
class ChatCompletion:
    """Normalized result from a chat-completions inference provider."""

    content: str
    raw: dict[str, Any]


class InferenceProvider(Protocol):
    """Shared interface available to every agent loop."""

    def __enter__(self) -> "InferenceProvider":
        """Open provider resources."""

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Close provider resources."""

    def complete(self, messages: Sequence[ChatMessage]) -> ChatCompletion:
        """Return a single assistant completion for the provided message history."""


class OpenAICompatibleInferenceProvider:
    """Inference provider for OpenAI-compatible chat-completions APIs."""

    def __init__(self, config: RuntimeConfig, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(timeout=config.request_timeout_seconds)
        self._owns_client = client is None

    def __enter__(self) -> "OpenAICompatibleInferenceProvider":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete(self, messages: Sequence[ChatMessage]) -> ChatCompletion:
        headers = {"Content-Type": "application/json"}
        if self._config.inference_api_key:
            headers["Authorization"] = f"Bearer {self._config.inference_api_key}"

        response = self._client.post(
            self._config.chat_completions_url,
            headers=headers,
            json={
                "model": self._config.inference_model,
                "messages": [dict(message) for message in messages],
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
            },
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError(
                f"{self._config.inference_provider} response did not include choices: {payload}"
            )
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            raise RuntimeError(
                f"{self._config.inference_provider} response did not include message content: {payload}"
            )
        return ChatCompletion(content=str(content).strip(), raw=payload)


def create_inference_provider(config: RuntimeConfig) -> InferenceProvider:
    provider = config.inference_provider
    if provider in {"litellm", "openai", "openai-compatible", "runpod", "zenith"}:
        return OpenAICompatibleInferenceProvider(config)
    raise ValueError(f"Unsupported INFERENCE_PROVIDER: {config.inference_provider!r}")
