from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from hub_runtime.core.config import RuntimeConfig
from hub_runtime.core.inference import OpenAICompatibleInferenceProvider


class RuntimeInferenceConfigTests(unittest.TestCase):
    def test_zenith_provider_reads_runpod_endpoint_settings(self) -> None:
        config = RuntimeConfig.from_env(
            {
                "INFERENCE_PROVIDER": "zenith",
                "ZENITH_OPENAI_BASE_URL": "https://api.runpod.ai/v2/endpoint/openai/v1",
                "RUNPOD_ENDPOINT_API_KEY": "endpoint-key",
                "ZENITH_MODEL": "gpt-oss-120b",
            }
        )

        self.assertEqual(config.inference_provider, "zenith")
        self.assertEqual(config.inference_base_url, "https://api.runpod.ai/v2/endpoint/openai/v1")
        self.assertEqual(
            config.chat_completions_url,
            "https://api.runpod.ai/v2/endpoint/openai/v1/chat/completions",
        )
        self.assertEqual(config.inference_api_key, "endpoint-key")
        self.assertEqual(config.inference_model, "gpt-oss-120b")

    def test_zenith_provider_can_read_deploy_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(
                json.dumps({"openai_base_url": "https://api.runpod.ai/v2/from-state/openai/v1"}),
                encoding="utf-8",
            )

            config = RuntimeConfig.from_env(
                {
                    "INFERENCE_PROVIDER": "zenith",
                    "ZENITH_STATE_FILE": str(state_path),
                    "RUNPOD_ENDPOINT_API_KEY": "endpoint-key",
                }
            )

        self.assertEqual(config.inference_base_url, "https://api.runpod.ai/v2/from-state/openai/v1")
        self.assertEqual(config.inference_model, "gpt-oss-120b")

    def test_openai_compatible_provider_sends_standard_chat_completion_request(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": " done "}}]},
            )

        config = RuntimeConfig.from_env(
            {
                "INFERENCE_PROVIDER": "zenith",
                "ZENITH_OPENAI_BASE_URL": "https://api.runpod.ai/v2/endpoint/openai/v1",
                "RUNPOD_ENDPOINT_API_KEY": "endpoint-key",
                "ZENITH_MODEL": "gpt-oss-120b",
                "HUB_TEMPERATURE": "0.1",
                "HUB_MAX_TOKENS": "128",
            }
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleInferenceProvider(config, client=client)
            completion = provider.complete([{"role": "user", "content": "hello"}])

        self.assertEqual(completion.content, "done")
        self.assertEqual(
            captured["url"],
            "https://api.runpod.ai/v2/endpoint/openai/v1/chat/completions",
        )
        self.assertEqual(captured["authorization"], "Bearer endpoint-key")
        self.assertEqual(
            captured["body"],
            {
                "model": "gpt-oss-120b",
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0.1,
                "max_tokens": 128,
            },
        )


if __name__ == "__main__":
    unittest.main()
