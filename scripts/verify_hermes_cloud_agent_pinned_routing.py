#!/usr/bin/env python3
"""Verify the local-routing patch against the exact pinned Hermes source."""

from __future__ import annotations

import argparse
import ast
import copy
import importlib.machinery
import importlib.util
import os
import py_compile
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

PINNED_COMMIT = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"
EXPECTED_MODEL = "qwen3-8b-q4-k-m"
EXPECTED_BASE_URL = "http://127.0.0.1:8080/v1"
ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "infra/hermes_cloud_agent/patches/strict-local-model-routing.patch"
VALIDATOR = ROOT / "infra/hermes_cloud_agent/runtime/hermes-validate-local-routing"
PATCHED_FILES = (
    "agent/auxiliary_client.py",
    "gateway/run.py",
    "gateway/slash_commands.py",
    "hermes_cli/runtime_provider.py",
)


class VerificationError(RuntimeError):
    pass


def _run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise VerificationError(result.stderr.strip() or "command failed")
    return result.stdout.strip()


def _load_validator() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("routing_validator_verify", str(VALIDATOR))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise VerificationError("validator import failed")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _dict_value(node: ast.Dict, key: str) -> ast.AST:
    for index, raw_key in enumerate(node.keys):
        value = node.values[index]
        if isinstance(raw_key, ast.Constant) and raw_key.value == key:
            return value
    raise VerificationError(f"missing dictionary key: {key}")


def _pinned_auxiliary_tasks(config_path: Path) -> set[str]:
    tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "DEFAULT_CONFIG" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            break
        auxiliary = _dict_value(node.value, "auxiliary")
        if not isinstance(auxiliary, ast.Dict):
            break
        tasks = {
            str(key.value)
            for key in auxiliary.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        tasks.discard("transient_retries")
        return tasks
    raise VerificationError("could not parse pinned auxiliary task set")


def _extract_function(path: Path, name: str, globals_dict: dict[str, Any]) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(
        (item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name),
        None,
    )
    if node is None:
        raise VerificationError(f"missing patched function: {name}")
    module = ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(node)], type_ignores=[]))
    namespace = dict(globals_dict)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def _verify_main_guard(path: Path) -> None:
    guard = _extract_function(path, "_enforce_strict_local_model_route", {"os": os})
    old = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(
            {
                "HERMES_STRICT_LOCAL_MODEL_ROUTING": "1",
                "HERMES_PINNED_MODEL": EXPECTED_MODEL,
                "HERMES_PINNED_BASE_URL": EXPECTED_BASE_URL,
            }
        )
        accepted = {
            "provider": "custom",
            "base_url": EXPECTED_BASE_URL,
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
        }
        assert guard(EXPECTED_MODEL, accepted) == (EXPECTED_MODEL, accepted)
        for mutated in (
            {**accepted, "provider": "openrouter"},
            {**accepted, "base_url": "https://example.invalid/v1"},
            {**accepted, "command": "remote-provider"},
            {**accepted, "api_key": "real-provider-credential"},
        ):
            try:
                guard(EXPECTED_MODEL, mutated)
            except RuntimeError as exc:
                if "local inference route mismatch" not in str(exc):
                    raise
            else:
                raise VerificationError("patched main guard accepted an alternate route")
        try:
            guard("other-model", accepted)
        except RuntimeError:
            pass
        else:
            raise VerificationError("patched main guard accepted an alternate model")
    finally:
        os.environ.clear()
        os.environ.update(old)


def _verify_runtime_request_guard(path: Path) -> None:
    configured = {
        "provider": "custom",
        "default": EXPECTED_MODEL,
        "base_url": EXPECTED_BASE_URL,
        "api_key": "",
    }
    guard = _extract_function(
        path,
        "_enforce_strict_local_runtime_request",
        {"os": os, "_get_model_config": lambda: dict(configured)},
    )
    old = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(
            {
                "HERMES_STRICT_LOCAL_MODEL_ROUTING": "1",
                "HERMES_PINNED_MODEL": EXPECTED_MODEL,
                "HERMES_PINNED_BASE_URL": EXPECTED_BASE_URL,
            }
        )
        guard(None, None, None, None)
        for arguments in (
            ("auto", None, None, None),
            ("openrouter", None, None, None),
            (None, "real-provider-credential", None, None),
            (None, None, "https://example.invalid/v1", None),
            (None, None, None, "other-model"),
        ):
            try:
                guard(*arguments)
            except RuntimeError as exc:
                if "local inference route mismatch" not in str(exc):
                    raise
            else:
                raise VerificationError("runtime resolver guard accepted an alternate route")
    finally:
        os.environ.clear()
        os.environ.update(old)


def _verify_auxiliary_guard(path: Path) -> None:
    guard = _extract_function(path, "_enforce_strict_auxiliary_route", {"os": os})
    old = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(
            {
                "HERMES_STRICT_LOCAL_MODEL_ROUTING": "1",
                "HERMES_PINNED_MODEL": EXPECTED_MODEL,
                "HERMES_PINNED_BASE_URL": EXPECTED_BASE_URL,
            }
        )
        guard("custom", EXPECTED_MODEL, EXPECTED_BASE_URL, "no-key-required")
        for arguments in (
            ("auto", EXPECTED_MODEL, EXPECTED_BASE_URL, ""),
            ("openrouter", EXPECTED_MODEL, EXPECTED_BASE_URL, ""),
            ("custom", "other-model", EXPECTED_BASE_URL, ""),
            ("custom", EXPECTED_MODEL, "https://example.invalid/v1", ""),
            ("custom", EXPECTED_MODEL, EXPECTED_BASE_URL, "real-provider-credential"),
        ):
            try:
                guard(*arguments)
            except RuntimeError as exc:
                if "local inference route mismatch" not in str(exc):
                    raise
            else:
                raise VerificationError("auxiliary resolver guard accepted an alternate route")
    finally:
        os.environ.clear()
        os.environ.update(old)


def verify(source: Path) -> None:
    if _run("git", "-C", str(source), "rev-parse", "HEAD") != PINNED_COMMIT:
        raise VerificationError("Hermes source is not the pinned commit")
    validator = _load_validator()
    pinned_tasks = _pinned_auxiliary_tasks(source / "hermes_cli/config.py")
    if pinned_tasks != set(validator.AUXILIARY_TASKS):
        raise VerificationError("validator auxiliary task set differs from pinned Hermes")

    with tempfile.TemporaryDirectory(prefix="hermes-routing-patch-") as raw_temp:
        patched = Path(raw_temp)
        for relative in PATCHED_FILES:
            destination = patched / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
        _run("git", "apply", "--check", str(PATCH), cwd=patched)
        _run("git", "apply", str(PATCH), cwd=patched)
        for relative in PATCHED_FILES:
            py_compile.compile(str(patched / relative), doraise=True)

        _verify_main_guard(patched / "gateway/run.py")
        _verify_runtime_request_guard(patched / "hermes_cli/runtime_provider.py")
        _verify_auxiliary_guard(patched / "agent/auxiliary_client.py")
        auxiliary_text = (patched / "agent/auxiliary_client.py").read_text(encoding="utf-8")
        slash_text = (patched / "gateway/slash_commands.py").read_text(encoding="utf-8")
        gateway_text = (patched / "gateway/run.py").read_text(encoding="utf-8")
        runtime_text = (patched / "hermes_cli/runtime_provider.py").read_text(encoding="utf-8")
        if auxiliary_text.count("local inference route mismatch") != 1:
            raise VerificationError("auxiliary fail-closed guard is missing or ambiguous")
        if "provider != \"custom\"" not in auxiliary_text:
            raise VerificationError("auxiliary provider guard is missing")
        if "base_url != expected_base_url" not in auxiliary_text:
            raise VerificationError("auxiliary endpoint guard is missing")
        if 'api_key not in {"", "no-key-required"}' not in auxiliary_text:
            raise VerificationError("auxiliary credential guard is missing")
        if "Model routing is pinned to local inference." not in slash_text:
            raise VerificationError("/model guard is missing")
        if "Model routing is pinned to local inference." not in gateway_text:
            raise VerificationError("/moa guard is missing")
        runtime_start = runtime_text.index("def resolve_runtime_provider")
        strict_call = runtime_text.index("_enforce_strict_local_runtime_request", runtime_start)
        provider_resolution = runtime_text.index("resolve_requested_provider", runtime_start)
        if strict_call > provider_resolution:
            raise VerificationError("runtime provider discovery precedes its strict route guard")
        override_start = gateway_text.index('if override_runtime.get("api_key"):')
        pool_lookup = gateway_text.index("_credential_pool_for_provider", override_start)
        override_guard = gateway_text.index(
            "_enforce_strict_local_model_route(override_model, override_runtime)",
            override_start,
        )
        if override_guard > pool_lookup:
            raise VerificationError("persisted override resolves credentials before its route guard")
        channel_start = gateway_text.index("if ch:")
        channel_resolver = gateway_text.index(
            "_resolve_runtime_agent_kwargs_for_provider", channel_start
        )
        channel_guard = gateway_text.index("HERMES_STRICT_LOCAL_MODEL_ROUTING", channel_start)
        if channel_guard > channel_resolver:
            raise VerificationError("channel override resolves a provider before its route guard")
        rehydrate_start = gateway_text.index("def _rehydrate_session_model_override")
        rehydrate_resolver = gateway_text.index(
            "_resolve_runtime_agent_kwargs_for_provider", rehydrate_start
        )
        rehydrate_guard = gateway_text.index(
            "HERMES_STRICT_LOCAL_MODEL_ROUTING", rehydrate_start
        )
        if rehydrate_guard > rehydrate_resolver:
            raise VerificationError("persisted override rehydrates credentials before its route guard")
        apply_start = gateway_text.index("def _apply_session_model_override")
        apply_pool_lookup = gateway_text.index("_credential_pool_for_provider", apply_start)
        apply_guard = gateway_text.index(
            "_enforce_strict_local_model_route", apply_start
        )
        if apply_guard > apply_pool_lookup:
            raise VerificationError("session override resolves a pool before its route guard")
        for method_name, resolver_text in (
            ("def _load_provider_routing", 'cfg.get("provider_routing"'),
            ("def _load_fallback_model", "fb = get_fallback_chain"),
            ("def _refresh_fallback_model", "self._fallback_model = get_fallback_chain"),
        ):
            method_start = gateway_text.index(method_name)
            strict_guard = gateway_text.index(
                "HERMES_STRICT_LOCAL_MODEL_ROUTING", method_start
            )
            resolver = gateway_text.index(resolver_text, method_start)
            if strict_guard > resolver:
                raise VerificationError(f"{method_name} reads mutable routing before its guard")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-source", type=Path, required=True)
    args = parser.parse_args()
    verify(args.hermes_source.resolve())
    print(f"pinned Hermes local-routing compatibility passed: {PINNED_COMMIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
