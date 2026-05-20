from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_model_profile_check_passes_for_project_h_contract() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/model_profile_check.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["blocker"] == "none"
    assert payload["deploy"] == {"changed_production": False, "started_local_containers": False}
    assert all(test["ok"] for test in payload["tests"])


def test_frank_prod_review_profile_resolves_to_internal_qwen_without_raw_secret() -> None:
    data = yaml.safe_load((ROOT / "infra/model-profiles.yaml").read_text())

    frank = data["agents"]["frank"]
    prod_binding = frank["profiles"]["review_brief_compiler"]["bindings"]["cloud-aws-prod"]

    assert prod_binding["provider"] == "hub-internal-openai-compatible"
    assert prod_binding["model"] == "Qwen3.5-9B-Q4_K_M.gguf"
    assert prod_binding["endpoint_ref"] == "prod-llama-server"
    assert prod_binding["secret_ref"] == "none"
    assert prod_binding["fallback_profile"] == "fallback_fast"
    assert "OPENAI_API_KEY" not in str(data)
    assert "sk-" not in str(data)
