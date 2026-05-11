from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import inspect
import json
import os
import signal
import sys
import tempfile
import time
from typing import Any, Callable, Optional

from libs.common.logging import get_logger

from .contracts import ToolManifest, ToolResult


def _read_config_secret_file() -> dict[str, str]:
    path = os.environ.get("HUB_CONFIG_SECRETS_PATH", "").strip()
    if not path or not os.path.exists(path):
        return {}
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def sanitized_env(*, request_id: str, tool_name: str, env_vars: list[str] | None = None) -> dict[str, str]:
    """Best-effort env sanitization: do not inherit the parent env by default."""
    path = os.environ.get(
        "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    env = {
        "PATH": path,
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "/app",
        "REQUEST_ID": request_id,
        "TOOL_NAME": tool_name,
    }
    config_values = _read_config_secret_file()
    for env_var in env_vars or []:
        value = os.environ.get(env_var)
        if value is None:
            value = config_values.get(env_var)
        if value is not None:
            env[env_var] = value
    return env


def _truncate_bytes(data: bytes, *, max_bytes: int) -> str:
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace")
    truncated = data[:max_bytes]
    suffix = f"\n... (truncated to {max_bytes} bytes)\n".encode("utf-8")
    keep = max(0, max_bytes - len(suffix))
    return (truncated[:keep] + suffix).decode("utf-8", errors="replace")


def _make_preexec_fn(*, max_memory_mb: int, timeout_ms: int) -> Callable[[], None]:
    def _fn() -> None:
        # Start a new process group so timeouts can kill the entire tree.
        os.setsid()

        # Best-effort resource limits (Linux). If unavailable, tool still runs.
        try:
            import resource

            bytes_limit = int(max_memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))

            cpu_seconds = max(1, int(timeout_ms / 1000) + 1)
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except Exception:
            pass

    return _fn


async def run_tool_subprocess(
    *,
    manifest: ToolManifest,
    tool_input: dict[str, Any],
    request_id: str,
    timeout_ms: Optional[int] = None,
    max_memory_mb: Optional[int] = None,
    stdout_max_bytes: int = 65_536,
    stderr_max_bytes: int = 65_536,
) -> ToolResult:
    log = get_logger()

    timeout_ms = int(timeout_ms or manifest.timeout_ms)
    max_memory_mb = int(max_memory_mb or manifest.max_memory_mb)

    started = time.monotonic()
    timed_out = False

    with tempfile.TemporaryDirectory(prefix=f"tool-{manifest.name}-") as workdir:
        cmd = [
            sys.executable,
            "-m",
            "libs.tools.sandbox_runner",
            "--entrypoint",
            manifest.entrypoint,
            "--request-id",
            request_id,
        ]
        env = sanitized_env(
            request_id=request_id,
            tool_name=manifest.name,
            env_vars=list(manifest.env_vars or []),
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env=env,
            preexec_fn=_make_preexec_fn(max_memory_mb=max_memory_mb, timeout_ms=timeout_ms),
        )

        stdout_b = b""
        stderr_b = b""
        try:
            stdin_b = json.dumps(tool_input).encode("utf-8")
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin_b), timeout=timeout_ms / 1000.0
            )
        except asyncio.TimeoutError:
            timed_out = True
            with contextlib.suppress(Exception):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                stdout_b, stderr_b = await proc.communicate()

        duration_ms = int((time.monotonic() - started) * 1000)
        exit_code = int(proc.returncode if proc.returncode is not None else -1)

        stdout_s = _truncate_bytes(stdout_b, max_bytes=stdout_max_bytes)
        stderr_s = _truncate_bytes(stderr_b, max_bytes=stderr_max_bytes)

        output: Optional[dict[str, Any]] = None
        success = False
        error_message = ""

        if stdout_s.strip():
            try:
                parsed = json.loads(stdout_s)
                if isinstance(parsed, dict) and parsed.get("ok") is True:
                    output_val = parsed.get("output")
                    if isinstance(output_val, dict):
                        output = output_val
                        success = (exit_code == 0) and (not timed_out)
                    else:
                        error_message = "tool output was not an object"
                elif isinstance(parsed, dict):
                    error_message = str(parsed.get("error") or "tool runner error")
                else:
                    error_message = "tool runner returned non-object JSON"
            except Exception:
                error_message = "failed to parse tool stdout as JSON"
        else:
            error_message = "tool produced no stdout"

        if timed_out:
            error_message = error_message or "tool timed out"

        log.info(
            "tool_subprocess_complete",
            tool_name=manifest.name,
            request_id=request_id,
            duration_ms=duration_ms,
            exit_code=exit_code,
            timed_out=timed_out,
            success=success,
        )

        return ToolResult(
            request_id=request_id,
            tool_name=manifest.name,
            success=success,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            output=output,
            stdout=stdout_s,
            stderr=stderr_s,
            error_message=error_message,
        )


def _import_entrypoint(entrypoint: str) -> Callable[..., Any]:
    if ":" not in entrypoint:
        raise ValueError("entrypoint must be in format module:function")
    module_name, func_name = entrypoint.split(":", 1)
    mod = importlib.import_module(module_name)
    fn = getattr(mod, func_name, None)
    if not callable(fn):
        raise ValueError("entrypoint function is not callable")
    return fn


def _invoke_entrypoint(fn: Callable[..., Any], *, tool_input: dict[str, Any], request_id: str) -> dict[str, Any]:
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    if "request_id" in sig.parameters:
        kwargs["request_id"] = request_id

    with contextlib.redirect_stdout(sys.stderr):
        if inspect.iscoroutinefunction(fn):
            result = asyncio.run(fn(tool_input, **kwargs))
        else:
            result = fn(tool_input, **kwargs)

    if not isinstance(result, dict):
        raise ValueError("tool must return an object (dict)")
    return result


def tool_runner_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tool entrypoint runner")
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--request-id", default="")
    args = parser.parse_args(argv)

    request_id = str(args.request_id or "")
    tool_input: dict[str, Any] = {}
    raw_stdin = sys.stdin.read()
    if raw_stdin.strip():
        tool_input = json.loads(raw_stdin)
        if not isinstance(tool_input, dict):
            raise ValueError("tool input must be an object (dict)")

    try:
        fn = _import_entrypoint(args.entrypoint)
        output = _invoke_entrypoint(fn, tool_input=tool_input, request_id=request_id)
        payload = {"ok": True, "output": output}
        sys.stdout.write(json.dumps(payload))
        sys.stdout.flush()
        return 0
    except Exception as e:
        payload = {"ok": False, "error": str(e)}
        sys.stdout.write(json.dumps(payload))
        sys.stdout.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(tool_runner_main())
