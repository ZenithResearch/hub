#!/usr/bin/env python3
"""Validate Hub external-root configuration and optional mounted root."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "infra/external-roots.yaml"
REQUIRED_NAMESPACES = [
    "tooling-cache",
    "tooling-state",
    "temp",
    "model-artifacts",
    "build-cache",
    "runtime-state",
]


def check(name: str, ok: bool, detail: str, data: dict | None = None) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "data": data or {}}


def run(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(args, cwd=cwd or ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return proc.returncode, proc.stdout


def main() -> int:
    tests: list[dict] = []
    data = yaml.safe_load(CONFIG.read_text()) if CONFIG.exists() else {}
    ontology = data.get("ontology", {}) if isinstance(data, dict) else {}
    tests.append(check("external_roots.config_exists", CONFIG.exists(), "config file exists"))
    tests.append(check(
        "external_roots.required_namespaces",
        all(name in ontology for name in REQUIRED_NAMESPACES),
        "all required external-root namespaces are present",
        {"missing": [name for name in REQUIRED_NAMESPACES if name not in ontology]},
    ))
    tf_cache = ontology.get("tooling-cache", {}).get("children", {}).get("terraform-plugin-cache", {})
    tests.append(check(
        "external_roots.terraform_plugin_cache_env",
        tf_cache.get("env") == "TF_PLUGIN_CACHE_DIR",
        "Terraform plugin cache is mapped to TF_PLUGIN_CACHE_DIR",
    ))

    root = os.environ.get("HUB_REMOTE_ROOT")
    candidates = data.get("roots", {}).get("external", {}).get("default_candidates", []) if isinstance(data, dict) else []
    if not root:
        root = next((candidate for candidate in candidates if Path(candidate).is_dir()), "")
    root_path = Path(root) if root else None
    tests.append(check("external_roots.root_detected", bool(root_path and root_path.is_dir()), "external root detected", {"root": str(root_path) if root_path else None}))

    if root_path and root_path.is_dir():
        missing_dirs = []
        for name in REQUIRED_NAMESPACES:
            rel = ontology.get(name, {}).get("path", name)
            path = root_path / rel
            if not path.exists():
                missing_dirs.append(str(path))
        tests.append(check("external_roots.namespace_dirs_exist", not missing_dirs, "namespace dirs exist", {"missing": missing_dirs}))

        probe = root_path / "temp" / ".hub-external-write-probe"
        try:
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok\n")
            executable = root_path / "temp" / ".hub-external-exec-probe.sh"
            executable.write_text("#!/bin/sh\necho ok\n")
            executable.chmod(0o700)
            code, out = run([str(executable)])
            probe.unlink(missing_ok=True)
            executable.unlink(missing_ok=True)
            tests.append(check("external_roots.write_exec", code == 0 and out.strip() == "ok", "external root supports write and executable probes"))
        except OSError as exc:
            tests.append(check("external_roots.write_exec", False, f"write/exec probe failed: {exc}"))

    ok = all(test["ok"] for test in tests)
    result = {
        "files": ["infra/external-roots.yaml", "infra/EXTERNAL_ROOTS.md", "scripts/hub_external_env.sh", "scripts/terraform_external.sh"],
        "tests": tests,
        "deploy": {"changed_production": False, "changed_local_runtime_data": False},
        "blocker": "none" if ok else "external root validation failed",
        "next": "Use scripts/terraform_external.sh for local Terraform validation; move Docker/model caches only after explicit migration decision.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
