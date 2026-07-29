from __future__ import annotations

import copy
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "infra/hermes_cloud_agent/runtime/hermes-validate-local-routing"
TERRAFORM = ROOT / "infra/aws_baseline_80/hermes_cloud_agent.tf"
BOOTSTRAP = ROOT / "infra/hermes_cloud_agent/bootstrap.sh.tftpl"
RUNNER = ROOT / "infra/hermes_cloud_agent/runtime/hermes-cloud-agent-run"
SERVICE = ROOT / "infra/hermes_cloud_agent/systemd/hermes-cloud-agent.service"
ROUTING_PATCH = ROOT / "infra/hermes_cloud_agent/patches/strict-local-model-routing.patch"

EXPECTED_BASE_URL = "http://127.0.0.1:8080/v1"
EXPECTED_MODEL = "qwen3-8b-q4-k-m"
EXPECTED_AUXILIARY_TASKS = {
    "approval",
    "background_review",
    "compression",
    "curator",
    "goal_judge",
    "kanban_decomposer",
    "mcp",
    "memory_query_rewrite",
    "moa_aggregator",
    "moa_reference",
    "monitor",
    "profile_describer",
    "skills_hub",
    "title_generation",
    "triage_specifier",
    "tts_audio_tags",
    "vision",
    "web_extract",
}


def _load_validator() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("hermes_local_routing_test", str(VALIDATOR))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _routing_block() -> dict[str, Any]:
    return {
        "provider": "custom",
        "model": EXPECTED_MODEL,
        "base_url": EXPECTED_BASE_URL,
        "api_key": "",
        "fallback_chain": [],
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, Any]]:
    lock = {
        "schema_version": 1,
        "desired": {
            "llama_cpp": {
                "commit": "47a39665e7081dc482feec169961acc09750a5c4",
                "archive_sha256": "8" * 64,
            },
            "model": {
                "model_id": EXPECTED_MODEL,
                "sha256": "d" * 64,
                "context_length": 32768,
            },
        },
    }
    lock_bytes = json.dumps(lock, sort_keys=True).encode()
    lock_sha256 = hashlib.sha256(lock_bytes).hexdigest()
    profile = {
        "inference": {
            "provider": "custom",
            "base_url": EXPECTED_BASE_URL,
            "model_id": EXPECTED_MODEL,
            "model_sha256": "d" * 64,
            "artifact_lock_sha256": lock_sha256,
            "fallbacks": [],
        }
    }
    config = {
        "model": {
            "provider": "custom",
            "default": EXPECTED_MODEL,
            "base_url": EXPECTED_BASE_URL,
            "api_key": "",
            "api_mode": "chat_completions",
            "context_length": 32768,
        },
        "fallback_providers": [],
        "fallback_model": [],
        "auxiliary": {
            "transient_retries": 2,
            **{task: _routing_block() for task in sorted(EXPECTED_AUXILIARY_TASKS)},
        },
    }
    ready = {
        "schema_version": 1,
        "active_role": "desired",
        "generation_id": "generation",
        "lock_sha256": lock_sha256,
        "runtime": {
            "commit": lock["desired"]["llama_cpp"]["commit"],
            "archive_sha256": lock["desired"]["llama_cpp"]["archive_sha256"],
        },
        "model": {
            "model_id": EXPECTED_MODEL,
            "sha256": "d" * 64,
        },
    }

    profile_path = tmp_path / "profile.json"
    config_path = tmp_path / "config.yaml"
    lock_path = tmp_path / "local-inference.lock.json"
    ready_path = tmp_path / "READY.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    lock_path.write_bytes(lock_bytes)
    ready_path.write_text(json.dumps(ready), encoding="utf-8")
    return profile_path, config_path, lock_path, ready_path, config


def _validate(tmp_path: Path, config: dict[str, Any], *, environ: dict[str, str] | None = None) -> None:
    module = _load_validator()
    profile_path, config_path, lock_path, ready_path, _ = _fixture(tmp_path)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    module.validate_local_routing(
        profile_path=profile_path,
        config_path=config_path,
        lock_path=lock_path,
        ready_path=ready_path,
        environ={} if environ is None else environ,
    )


def test_exact_main_and_every_pinned_auxiliary_route_are_accepted(tmp_path: Path) -> None:
    module = _load_validator()
    profile_path, config_path, lock_path, ready_path, config = _fixture(tmp_path)

    module.validate_local_routing(
        profile_path=profile_path,
        config_path=config_path,
        lock_path=lock_path,
        ready_path=ready_path,
        environ={},
    )

    assert set(module.AUXILIARY_TASKS) == EXPECTED_AUXILIARY_TASKS
    assert set(config["auxiliary"]) == EXPECTED_AUXILIARY_TASKS | {"transient_retries"}


def test_routing_validator_rejects_coherent_alternate_model_generation(tmp_path: Path) -> None:
    module = _load_validator()
    profile_path, config_path, lock_path, ready_path, config = _fixture(tmp_path)
    alternate_model = "internally-consistent-but-unapproved"

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["desired"]["model"]["model_id"] = alternate_model
    lock_bytes = json.dumps(lock, sort_keys=True).encode()
    lock_path.write_bytes(lock_bytes)

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["inference"]["model_id"] = alternate_model
    profile["inference"]["artifact_lock_sha256"] = hashlib.sha256(lock_bytes).hexdigest()
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    config["model"]["default"] = alternate_model
    for task in EXPECTED_AUXILIARY_TASKS:
        config["auxiliary"][task]["model"] = alternate_model
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    ready = json.loads(ready_path.read_text(encoding="utf-8"))
    ready["model"]["model_id"] = alternate_model
    ready["lock_sha256"] = hashlib.sha256(lock_bytes).hexdigest()
    ready_path.write_text(json.dumps(ready), encoding="utf-8")

    with pytest.raises(Exception, match="model identity"):
        module.validate_local_routing(
            profile_path=profile_path,
            config_path=config_path,
            lock_path=lock_path,
            ready_path=ready_path,
            environ={},
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("model", "provider"), "auto"),
        (("model", "provider"), "main"),
        (("model", "provider"), "openrouter"),
        (("model", "default"), "other-model"),
        (("model", "base_url"), "http://localhost:8080/v1"),
        (("model", "base_url"), "https://example.invalid/v1"),
        (("model", "api_key"), "secret"),
        (("model", "api_mode"), "codex_responses"),
        (("fallback_providers",), [{"provider": "openrouter", "model": "fallback"}]),
        (("fallback_model",), {"provider": "anthropic", "model": "fallback"}),
        (("auxiliary", "vision", "provider"), "auto"),
        (("auxiliary", "vision", "provider"), "main"),
        (("auxiliary", "vision", "base_url"), "https://example.invalid/v1"),
        (("auxiliary", "vision", "model"), "other-model"),
        (("auxiliary", "vision", "api_key"), "secret"),
        (("auxiliary", "vision", "fallback_chain"), [{"provider": "nous"}]),
    ],
)
def test_routing_validator_rejects_discovery_remote_credentials_and_fallback(
    tmp_path: Path, path: tuple[str, ...], value: object
) -> None:
    *_, config = _fixture(tmp_path)
    mutated = copy.deepcopy(config)
    target: Any = mutated
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(Exception, match="local routing"):
        _validate(tmp_path, mutated)


@pytest.mark.parametrize("task", sorted(EXPECTED_AUXILIARY_TASKS))
def test_routing_validator_requires_every_pinned_auxiliary_task(
    tmp_path: Path, task: str
) -> None:
    *_, config = _fixture(tmp_path)
    config["auxiliary"].pop(task)

    with pytest.raises(Exception, match="auxiliary"):
        _validate(tmp_path, config)


@pytest.mark.parametrize(
    "extra",
    [
        {"providers": {"remote": {"base_url": "https://example.invalid/v1"}}},
        {"custom_providers": [{"name": "remote", "base_url": "https://example.invalid/v1"}]},
        {"provider_routing": {"order": ["openrouter"]}},
        {"channel_overrides": {"matrix": {"provider": "openrouter"}}},
        {"moa": {"default_preset": "remote"}},
    ],
)
def test_routing_validator_rejects_alternate_model_routing_surfaces(
    tmp_path: Path, extra: dict[str, Any]
) -> None:
    *_, config = _fixture(tmp_path)
    config.update(extra)

    with pytest.raises(Exception, match="routing surface"):
        _validate(tmp_path, config)


@pytest.mark.parametrize(
    "environment",
    [
        {"HTTP_PROXY": "http://proxy.invalid:3128"},
        {"NO_PROXY": "127.0.0.1"},
        {"OPENAI_API_KEY": "secret"},
        {"OPENROUTER_API_KEY": "secret"},
        {"ANTHROPIC_API_KEY": "secret"},
        {"AWS_BEARER_TOKEN_BEDROCK": "secret"},
        {"OPENAI_BASE_URL": "https://example.invalid/v1"},
        {"AUXILIARY_VISION_PROVIDER": "auto"},
        {"CEREBRAS_API_KEY": "secret"},
        {"CLAUDE_CODE_OAUTH_TOKEN": "secret"},
    ],
)
def test_routing_validator_rejects_proxy_and_provider_environment(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    *_, config = _fixture(tmp_path)

    with pytest.raises(Exception, match="environment"):
        _validate(tmp_path, config, environ=environment)


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env",
        "auth.json",
        "credentials.json",
        ".codex/auth.json",
        ".claude/.credentials.json",
        ".config/github-copilot/hosts.json",
        ".minimax/credentials.json",
    ],
)
def test_routing_validator_rejects_persisted_provider_credentials(
    tmp_path: Path, relative_path: str
) -> None:
    module = _load_validator()
    profile_path, config_path, lock_path, ready_path, _ = _fixture(tmp_path)
    credential_path = config_path.parent / relative_path
    credential_path.parent.mkdir(parents=True, exist_ok=True)
    credential_path.write_text("credential-state", encoding="utf-8")

    with pytest.raises(Exception, match="credential state"):
        module.validate_local_routing(
            profile_path=profile_path,
            config_path=config_path,
            lock_path=lock_path,
            ready_path=ready_path,
            environ={},
        )


def test_c44_wires_validator_strict_upstream_patch_and_environment_scrubbing() -> None:
    terraform = TERRAFORM.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    patch = ROUTING_PATCH.read_text(encoding="utf-8")

    for task in EXPECTED_AUXILIARY_TASKS:
        assert f'"{task}"' in terraform
    assert "fallback_providers = []" in terraform
    assert "fallback_model     = []" in terraform
    assert "routing_validator_b64" in terraform
    assert "routing_patch_b64" in terraform
    assert "hermes-validate-local-routing" in bootstrap
    assert "strict-local-model-routing.patch" in bootstrap
    assert "git -C \"$HERMES_SOURCE\" apply --check" in bootstrap
    assert "hermes-validate-local-routing" in runner
    assert "HERMES_STRICT_LOCAL_MODEL_ROUTING=1" in runner
    assert "HERMES_PINNED_MODEL" in runner
    assert "HERMES_PINNED_BASE_URL" in runner
    assert "AWS_EC2_METADATA_DISABLED=true" in runner
    assert "AWS_SHARED_CREDENTIALS_FILE=/dev/null" in runner
    assert "AWS_CONFIG_FILE=/dev/null" in runner
    assert runner.index("for name in") < runner.index("hermes-validate-local-routing")
    assert runner.index("hermes-validate-local-routing") < runner.index(
        "hermes-read-matrix-secret"
    )
    assert runner.index("hermes-read-matrix-secret") < runner.index(
        "AWS_EC2_METADATA_DISABLED=true"
    )
    assert runner.index("AWS_EC2_METADATA_DISABLED=true") < runner.index(
        'exec "$HERMES_BIN"'
    )
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "OPENAI_API_KEY"):
        assert name in service
    assert "HERMES_STRICT_LOCAL_MODEL_ROUTING" in patch
    assert "local inference route mismatch" in patch
    assert 'runtime.get("api_key") not in {None, "", "no-key-required"}' in patch
    assert 'api_key not in {"", "no-key-required"}' in patch
    assert "_enforce_strict_local_runtime_request" in patch


def test_gateway_service_can_read_only_the_group_published_inference_attestations() -> None:
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    preparer = (
        ROOT / "infra/hermes_cloud_agent/runtime/hermes-prepare-local-inference"
    ).read_text(encoding="utf-8")

    assert "User=hermes" in service
    assert "Group=hermes" in service
    assert "SupplementaryGroups=hermes-inference" in service
    assert "chown root:hermes-inference /etc/hermes-cloud-agent/local-inference.lock.json" in bootstrap
    assert "chmod 0440 /etc/hermes-cloud-agent/local-inference.lock.json" in bootstrap
    assert 'grp.getgrnam("hermes-inference").gr_gid' in preparer
    assert "state_root.mkdir(mode=0o750" in preparer
    assert "mode=0o640" in preparer
