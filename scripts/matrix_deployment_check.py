#!/usr/bin/env python3
"""Static Matrix/Synapse deployment parity checks.

This is intentionally non-destructive: it does not start Docker containers,
rotate tokens, mutate .env, or touch tracked Matrix templates.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_LOCAL_FILES = [
    "infra/matrix/docker-compose.yml",
    "infra/matrix/config/homeserver.yaml",
    "infra/matrix/appservices/gateway-bot.yaml",
    "infra/matrix/appservices/bridge-bot.yaml",
    "scripts/setup_matrix_bots.sh",
    "scripts/setup_matrix_sophia.sh",
    "scripts/start.sh",
]


def run(args: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode, proc.stdout


def check(name: str, ok: bool, detail: str, data: dict | None = None) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "data": data or {}}


def main() -> int:
    tests: list[dict] = []

    missing = [path for path in REQUIRED_LOCAL_FILES if not (ROOT / path).exists()]
    tests.append(check("matrix.required_files", not missing, "all required files present" if not missing else "missing files", {"missing": missing}))

    homeserver = (ROOT / "infra/matrix/config/homeserver.yaml").read_text()
    tests.append(check(
        "matrix.homeserver_template_appservices_empty",
        "app_service_config_files: []" in homeserver,
        "tracked homeserver template keeps appservice list empty",
    ))
    tests.append(check(
        "matrix.homeserver_signing_key_data",
        'signing_key_path: "/data/${MATRIX_SERVER_NAME}.signing.key"' in homeserver,
        "signing key remains in /data runtime volume",
    ))

    compose = (ROOT / "infra/matrix/docker-compose.yml").read_text()
    compose_expectations = {
        "renders_to_data": "/data/homeserver.rendered.yaml" in compose,
        "generated_appservices_in_data": "/data/appservices" in compose and ".resolved.yaml" in compose,
        "repairs_data_ownership": "chown -R 991:991 /data" in compose,
        "sophia_gated": "MATRIX_REGISTER_SOPHIA_APP_SERVICE" in compose,
        "synapse_uses_rendered_config": "SYNAPSE_CONFIG_PATH: /data/homeserver.rendered.yaml" in compose,
    }
    for key, ok in compose_expectations.items():
        tests.append(check(f"matrix.compose.{key}", ok, key.replace("_", " ")))

    setup_bots = (ROOT / "scripts/setup_matrix_bots.sh").read_text()
    forbidden_setup_patterns = [
        r"HOMESERVER_YAML=",
        r"infra/matrix/config/homeserver\.yaml.*>>",
        r"gateway-bot\.resolved\.yaml",
        r"bridge-bot\.resolved\.yaml",
        r"sophia\.resolved\.yaml",
    ]
    setup_findings = [pat for pat in forbidden_setup_patterns if re.search(pat, setup_bots)]
    tests.append(check(
        "matrix.setup_does_not_mutate_tracked_templates",
        not setup_findings,
        "setup scripts leave tracked templates untouched" if not setup_findings else "forbidden tracked-template mutation pattern found",
        {"patterns": setup_findings},
    ))

    start_sh = (ROOT / "scripts/start.sh").read_text()
    tests.append(check(
        "matrix.start_sophia_receiver_when_registered",
        "HUB_SERVICES+=(ingest)" in start_sh and "MATRIX_REGISTER_SOPHIA_APP_SERVICE" in start_sh,
        "start.sh starts ingest when Sophia appservice is explicitly registered",
    ))

    for script in ["scripts/start.sh", "scripts/setup_matrix_bots.sh", "scripts/setup_matrix_sophia.sh"]:
        code, out = run(["bash", "-n", script])
        tests.append(check(f"matrix.shell_syntax.{script}", code == 0, "bash -n passed" if code == 0 else out[-500:]))

    docker_env = {
        "MATRIX_DB_PASSWORD": "dummy_matrix_db_password",
        "MATRIX_SERVER_NAME": "localhost",
        "MATRIX_REGISTRATION_SECRET": "dummy_registration_secret",
        "MATRIX_MACAROON_SECRET": "dummy_macaroon_secret",
        "MATRIX_FORM_SECRET": "dummy_form_secret",
    }
    code, out = run(["docker", "compose", "-f", "infra/matrix/docker-compose.yml", "config", "--quiet"], env=docker_env)
    tests.append(check(
        "matrix.compose_config",
        code == 0,
        "docker compose config passed" if code == 0 else out[-1000:],
    ))

    ok = all(test["ok"] for test in tests)
    result = {
        "files": ["scripts/matrix_deployment_check.py", "infra/matrix/DEPLOYMENT_PARITY.md"],
        "tests": tests,
        "deploy": {"changed_production": False, "started_local_containers": False},
        "blocker": "none" if ok else "matrix deployment parity check failed",
        "next": "Choose Matrix target: local/self-hosted hardening, AWS EC2 adoption, or cloud-prod Matrix deployment plan.",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
