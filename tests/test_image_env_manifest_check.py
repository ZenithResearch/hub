from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "image_env_manifest_check.py"
MANIFEST_PATH = ROOT / "infra" / "image-env-manifest.yaml"
ECS_PATH = ROOT / "infra" / "aws_baseline_80" / "ecs.tf"


def load_module():
    spec = importlib.util.spec_from_file_location("image_env_manifest_check", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_covers_every_ecs_task_definition_image_and_env_contract():
    module = load_module()

    result = module.run_checks(manifest_path=MANIFEST_PATH, ecs_path=ECS_PATH)

    assert result["ok"] is True, result
    service_names = {service["service"] for service in result["services"]}
    assert {
        "gateway",
        "runtime",
        "sandbox",
        "queue",
        "cases",
        "eventbus",
        "frank",
        "stt_http",
        "llama_server",
        "llama_model_preload",
    }.issubset(service_names)


def test_manifest_declares_secret_manager_backed_runtime_secrets():
    module = load_module()

    manifest = module.load_manifest(MANIFEST_PATH)
    by_service = {service["service"]: service for service in manifest["services"]}

    assert by_service["gateway"]["secrets"]["REVIEW_ACCESS_ADMIN_TOKEN"]["source"] == "aws_secrets_manager"
    assert by_service["runtime"]["secrets"]["QDRANT_API_KEY"]["source"] == "aws_secrets_manager"
    assert by_service["frank"]["secrets"]["ELEVENLABS_API_KEY"]["source"] == "aws_secrets_manager"


def test_missing_new_ecs_image_fails_manifest_check(tmp_path):
    module = load_module()
    modified_ecs = tmp_path / "ecs.tf"
    modified_ecs.write_text(
        ECS_PATH.read_text(encoding="utf-8")
        + '''\nresource "aws_ecs_task_definition" "new_worker" {\n  container_definitions = jsonencode([{\n    name = "app"\n    image = "example/new-worker:latest"\n    environment = [{ name = "NEW_REQUIRED_ENV", value = "1" }]\n  }])\n}\n''',
        encoding="utf-8",
    )

    result = module.run_checks(manifest_path=MANIFEST_PATH, ecs_path=modified_ecs)

    assert result["ok"] is False
    assert any("new_worker" in failure for failure in result["failures"]), result
