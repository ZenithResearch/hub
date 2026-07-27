import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import signal
import socket
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.error import URLError
from urllib.request import Request

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "infra/hermes_cloud_agent/runtime/hermes-inference-supervisor"
LOCK = ROOT / "infra/hermes_cloud_agent/artifacts/local-inference.lock.json"
SCHEMA = ROOT / "infra/hermes_cloud_agent/artifacts/local-inference-lock.schema.json"


def _load_supervisor() -> ModuleType:
    assert SUPERVISOR.is_file(), "C4.3 inference supervisor is not implemented"
    loader = importlib.machinery.SourceFileLoader("hermes_inference_supervisor_test", str(SUPERVISOR))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _generation_id(generation: dict[str, object]) -> str:
    canonical = json.dumps(generation, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _materialize_generation(
    tmp_path: Path,
    *,
    active_role: str = "desired",
    declare_rollback: bool = False,
) -> tuple[Path, Path, Path, Path, Path]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    model_bytes = b"pinned-model-fixture"
    lock["desired"]["model"]["sha256"] = hashlib.sha256(model_bytes).hexdigest()
    lock["desired"]["model"]["size_bytes"] = len(model_bytes)
    if declare_rollback:
        lock["rollback"] = copy.deepcopy(lock["desired"])
    generation = lock[active_role]
    lock_path = tmp_path / "local-inference.lock.json"
    schema_path = tmp_path / "local-inference-lock.schema.json"
    lock_path.write_text(json.dumps(lock, sort_keys=True), encoding="utf-8")
    schema_path.write_bytes(SCHEMA.read_bytes())

    runtime = generation["llama_cpp"]
    model = generation["model"]
    runtime_root = tmp_path / "runtime"
    server = (
        runtime_root
        / runtime["commit"]
        / runtime["archive_sha256"]
        / f"llama-{runtime['release']}"
        / "llama-server"
    )
    server.parent.mkdir(parents=True)
    runtime_target = runtime_root / runtime["commit"] / runtime["archive_sha256"]
    for directory in (runtime_root, runtime_target.parent, runtime_target, server.parent):
        directory.chmod(0o755)
    server_bytes = b"pinned-llama-server"
    server.write_bytes(server_bytes)
    server.chmod(0o755)
    manifest = {
        "schema_version": 1,
        "commit": runtime["commit"],
        "release": runtime["release"],
        "archive_sha256": runtime["archive_sha256"],
        "size_bytes": runtime["size_bytes"],
        "s3_version_id": runtime["s3_version_id"],
        "files": [
            {"path": ".", "type": "directory", "mode": 0o755},
            {
                "path": f"llama-{runtime['release']}",
                "type": "directory",
                "mode": 0o755,
            },
            {
                "path": f"llama-{runtime['release']}/llama-server",
                "type": "file",
                "sha256": hashlib.sha256(server_bytes).hexdigest(),
                "size_bytes": len(server_bytes),
                "mode": 0o755,
            },
        ],
    }
    (runtime_target / "RUNTIME.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    (runtime_target / "RUNTIME.json").chmod(0o644)

    model_root = tmp_path / "models"
    model_root.mkdir(mode=0o750)
    model_path = model_root / f"{model['sha256']}.gguf"
    model_path.write_bytes(model_bytes)
    model_path.chmod(0o440)

    lock_sha256 = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    ready = {
        "schema_version": 1,
        "active_role": active_role,
        "generation_id": _generation_id(generation),
        "lock_sha256": lock_sha256,
        "runtime": {
            "commit": runtime["commit"],
            "archive_sha256": runtime["archive_sha256"],
            "size_bytes": runtime["size_bytes"],
            "s3_version_id": runtime["s3_version_id"],
        },
        "model": {
            "model_id": model["model_id"],
            "sha256": model["sha256"],
            "size_bytes": model["size_bytes"],
            "s3_version_id": model["s3_version_id"],
        },
    }
    if active_role == "rollback":
        ready["failed_desired_generation_id"] = _generation_id(lock["desired"])
    ready_path = tmp_path / "READY.json"
    ready_path.write_text(json.dumps(ready), encoding="utf-8")
    return lock_path, schema_path, ready_path, runtime_root, model_root


def test_supervisor_resolves_only_the_exact_ready_generation(tmp_path: Path) -> None:
    module = _load_supervisor()
    lock_path, schema_path, ready_path, runtime_root, model_root = _materialize_generation(
        tmp_path
    )

    generation = module.load_generation(
        lock_path=lock_path,
        schema_path=schema_path,
        ready_path=ready_path,
        runtime_root=runtime_root,
        model_root=model_root,
    )

    assert generation.active_role == "desired"
    assert generation.model_alias == "qwen3-8b-q4-k-m"
    assert generation.server.name == "llama-server"
    assert generation.model.name == (
        f"{hashlib.sha256(b'pinned-model-fixture').hexdigest()}.gguf"
    )


def test_supervisor_accepts_only_an_explicit_ready_rollback_generation(
    tmp_path: Path,
) -> None:
    module = _load_supervisor()
    lock_path, schema_path, ready_path, runtime_root, model_root = _materialize_generation(
        tmp_path, active_role="rollback", declare_rollback=True
    )

    generation = module.load_generation(
        lock_path=lock_path,
        schema_path=schema_path,
        ready_path=ready_path,
        runtime_root=runtime_root,
        model_root=model_root,
    )

    assert generation.active_role == "rollback"


@pytest.mark.parametrize("failure", ["stale_lock", "undeclared_rollback", "oversized_ready"])
def test_supervisor_rejects_stale_undeclared_or_oversized_ready_state(
    tmp_path: Path, failure: str
) -> None:
    module = _load_supervisor()
    lock_path, schema_path, ready_path, runtime_root, model_root = _materialize_generation(
        tmp_path
    )
    if failure == "stale_lock":
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        ready["lock_sha256"] = "0" * 64
        ready_path.write_text(json.dumps(ready), encoding="utf-8")
    elif failure == "oversized_ready":
        ready_path.write_bytes(b"{" + b" " * (module.MAX_READY_BYTES + 1))
    else:
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        ready["active_role"] = "rollback"
        ready["failed_desired_generation_id"] = ready["generation_id"]
        ready_path.write_text(json.dumps(ready), encoding="utf-8")

    with pytest.raises(module.InferenceServiceError):
        module.load_generation(
            lock_path=lock_path,
            schema_path=schema_path,
            ready_path=ready_path,
            runtime_root=runtime_root,
            model_root=model_root,
        )


def test_supervisor_rejects_symlinked_runtime_or_model(tmp_path: Path) -> None:
    module = _load_supervisor()
    lock_path, schema_path, ready_path, runtime_root, model_root = _materialize_generation(
        tmp_path
    )
    generation = module.load_generation(
        lock_path=lock_path,
        schema_path=schema_path,
        ready_path=ready_path,
        runtime_root=runtime_root,
        model_root=model_root,
    )
    original = generation.server.with_suffix(".original")
    generation.server.rename(original)
    generation.server.symlink_to(original)

    with pytest.raises(module.InferenceServiceError):
        module.load_generation(
            lock_path=lock_path,
            schema_path=schema_path,
            ready_path=ready_path,
            runtime_root=runtime_root,
            model_root=model_root,
        )


@pytest.mark.parametrize("artifact", ["runtime", "model"])
def test_supervisor_rejects_same_size_artifact_content_corruption(
    tmp_path: Path, artifact: str
) -> None:
    module = _load_supervisor()
    lock_path, schema_path, ready_path, runtime_root, model_root = _materialize_generation(
        tmp_path
    )
    if artifact == "runtime":
        target = next(runtime_root.rglob("llama-server"))
        mode = 0o755
    else:
        target = next(model_root.glob("*.gguf"))
        mode = 0o440
    original = target.read_bytes()
    target.chmod(0o600)
    target.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])
    target.chmod(mode)

    with pytest.raises(module.InferenceServiceError):
        module.load_generation(
            lock_path=lock_path,
            schema_path=schema_path,
            ready_path=ready_path,
            runtime_root=runtime_root,
            model_root=model_root,
        )


def test_supervisor_rejects_a_preexisting_listener_before_launch() -> None:
    module = _load_supervisor()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with pytest.raises(module.InferenceServiceError):
            module._ensure_listener_available(port=port)


def test_supervisor_proves_the_listener_socket_is_owned_by_the_spawned_pid(
    tmp_path: Path,
) -> None:
    module = _load_supervisor()
    pid = 4242
    proc_pid = tmp_path / str(pid)
    (proc_pid / "fd").mkdir(parents=True)
    (proc_pid / "net").mkdir()
    os.symlink("socket:[98765]", proc_pid / "fd/7")
    (proc_pid / "net/tcp").write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
        "retrnsmt   uid  timeout inode\n"
        "   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 "
        "00000000  998 0 98765 1 0000000000000000\n",
        encoding="utf-8",
    )

    assert module._listener_owned_by_pid(pid, proc_root=tmp_path)
    (proc_pid / "fd/7").unlink()
    os.symlink("socket:[11111]", proc_pid / "fd/7")
    assert not module._listener_owned_by_pid(pid, proc_root=tmp_path)


def test_supervisor_rejects_an_oversized_proc_tcp_table(tmp_path: Path) -> None:
    module = _load_supervisor()
    pid = 4242
    proc_pid = tmp_path / str(pid)
    (proc_pid / "fd").mkdir(parents=True)
    (proc_pid / "net").mkdir()
    os.symlink("socket:[98765]", proc_pid / "fd/7")
    row = (
        "   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 "
        "00000000  998 0 98765 1 0000000000000000\n"
    ).encode()
    (proc_pid / "net/tcp").write_bytes(
        b"sl local_address rem_address st tx rx tr when retr uid timeout inode\n"
        + row * ((module.MAX_PROC_TCP_BYTES // len(row)) + 2)
    )

    assert not module._listener_owned_by_pid(pid, proc_root=tmp_path)


def test_supervisor_never_notifies_after_listener_ownership_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_supervisor()
    notifications: list[str] = []
    process = SimpleNamespace(pid=4242, poll=lambda: None)
    monkeypatch.setattr(module, "_listener_owned_by_pid", lambda _pid: False)
    monkeypatch.setattr(module, "_notify_systemd", notifications.append)

    with pytest.raises(module.InferenceServiceError):
        module._notify_if_listener_owned(process, "READY=1", stopping=lambda: False)

    assert notifications == []


@pytest.mark.parametrize("message", ["READY=1", "WATCHDOG=1"])
def test_supervisor_never_notifies_when_stop_arrives_during_listener_check(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    module = _load_supervisor()
    notifications: list[str] = []
    stop_requested = False
    process = SimpleNamespace(pid=4242, poll=lambda: None)

    def listener_check(_pid: int) -> bool:
        nonlocal stop_requested
        stop_requested = True
        return True

    monkeypatch.setattr(module, "_listener_owned_by_pid", listener_check)
    monkeypatch.setattr(module, "_notify_systemd", notifications.append)

    with pytest.raises(module.InferenceServiceError):
        module._notify_if_listener_owned(
            process, message, stopping=lambda: stop_requested
        )

    assert notifications == []


def test_supervisor_never_notifies_while_a_stop_signal_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_supervisor()
    notifications: list[str] = []
    process = SimpleNamespace(pid=4242, poll=lambda: None)
    monkeypatch.setattr(module, "_listener_owned_by_pid", lambda _pid: True)
    monkeypatch.setattr(module, "_notify_systemd", notifications.append)
    monkeypatch.setattr(module.signal, "sigpending", lambda: {signal.SIGTERM})

    with pytest.raises(module.InferenceServiceError):
        module._notify_if_listener_owned(
            process, "READY=1", stopping=lambda: False
        )

    assert notifications == []


def test_server_command_is_fixed_loopback_only_and_resource_bounded(tmp_path: Path) -> None:
    module = _load_supervisor()
    lock_path, schema_path, ready_path, runtime_root, model_root = _materialize_generation(
        tmp_path
    )
    generation = module.load_generation(
        lock_path=lock_path,
        schema_path=schema_path,
        ready_path=ready_path,
        runtime_root=runtime_root,
        model_root=model_root,
    )

    command = module.build_server_command(generation)

    assert command == [
        str(generation.server),
        "--model",
        str(generation.model),
        "--alias",
        "qwen3-8b-q4-k-m",
        "--host",
        "127.0.0.1",
        "--port",
        "8080",
        "--ctx-size",
        "32768",
        "--threads",
        "8",
        "--threads-batch",
        "8",
        "--threads-http",
        "2",
        "--parallel",
        "1",
        "--timeout",
        "120",
        "--jinja",
        "--no-webui",
        "--no-slots",
        "--log-disable",
    ]
    assert "0.0.0.0" not in command
    assert "--model-url" not in command


class _FakeResponse(io.BytesIO):
    def __init__(self, payload: dict[str, object] | bytes, status: int = 200) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _FakeOpener:
    def __init__(self, responses: dict[tuple[str, str], _FakeResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[Request] = []

    def open(self, request: Request, *, timeout: int):
        assert timeout == 10
        assert request.method is not None
        self.requests.append(request)
        response = self.responses[(request.method, request.full_url)]
        if isinstance(response, Exception):
            raise response
        return response


def _valid_probe_responses(
    alias: str = "qwen3-8b-q4-k-m",
) -> dict[tuple[str, str], _FakeResponse | Exception]:
    base = "http://127.0.0.1:8080"
    return {
        ("GET", f"{base}/health"): _FakeResponse({"status": "ok"}),
        ("GET", f"{base}/v1/models"): _FakeResponse(
            {"object": "list", "data": [{"id": alias, "object": "model"}]}
        ),
        ("POST", f"{base}/v1/chat/completions"): _FakeResponse(
            {
                "model": alias,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "health_probe",
                                        "arguments": '{"value":"ready"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
            }
        ),
    }


def test_readiness_probe_checks_health_model_and_nonsecret_tool_shape() -> None:
    module = _load_supervisor()
    opener = _FakeOpener(_valid_probe_responses())

    module.probe_ready(opener)

    assert [request.full_url for request in opener.requests] == [
        "http://127.0.0.1:8080/health",
        "http://127.0.0.1:8080/v1/models",
        "http://127.0.0.1:8080/v1/chat/completions",
    ]
    assert opener.requests[-1].data is not None
    assert isinstance(opener.requests[-1].data, bytes)
    probe = json.loads(opener.requests[-1].data)
    assert probe["model"] == "qwen3-8b-q4-k-m"
    assert probe["messages"] == [
        {"role": "user", "content": "Call health_probe with value ready."}
    ]
    assert probe["tool_choice"] == {
        "type": "function",
        "function": {"name": "health_probe"},
    }
    assert probe["max_tokens"] == 32
    assert "authorization" not in {
        key.lower() for request in opener.requests for key in request.headers
    }


@pytest.mark.parametrize("failure", ["wrong_alias", "oversized_health", "unavailable", "bad_tool"])
def test_readiness_probe_fails_closed_on_invalid_or_unavailable_responses(
    failure: str,
) -> None:
    module = _load_supervisor()
    responses = _valid_probe_responses()
    base = "http://127.0.0.1:8080"
    if failure == "wrong_alias":
        responses[("GET", f"{base}/v1/models")] = _FakeResponse(
            {"object": "list", "data": [{"id": "wrong-model"}]}
        )
    elif failure == "oversized_health":
        responses[("GET", f"{base}/health")] = _FakeResponse(
            b"{" + b" " * (module.MAX_RESPONSE_BYTES + 1)
        )
    elif failure == "unavailable":
        responses[("GET", f"{base}/health")] = URLError("unavailable")
    else:
        responses[("POST", f"{base}/v1/chat/completions")] = _FakeResponse(
            {"model": "qwen3-8b-q4-k-m", "choices": [{"message": {}}]}
        )

    with pytest.raises(module.InferenceServiceError):
        module.probe_ready(_FakeOpener(responses))


def test_http_client_disables_proxies_and_redirects() -> None:
    module = _load_supervisor()
    opener = module.make_http_client()

    source = SUPERVISOR.read_text(encoding="utf-8")
    assert "ProxyHandler({})" in source
    redirect = next(
        handler
        for handler in opener.handlers
        if handler.__class__.__name__ == "_RejectRedirect"
    )
    with pytest.raises(module.InferenceServiceError):
        redirect.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:8080/other")


def test_supervisor_emits_bounded_systemd_ready_and_watchdog_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_supervisor()
    assert "notifier.setblocking(False)" in SUPERVISOR.read_text(encoding="utf-8")
    notify_path = Path(f"/tmp/hermes-notify-{os.getpid()}.sock")
    notify_path.unlink(missing_ok=True)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            receiver.bind(str(notify_path))
            receiver.settimeout(1)
            monkeypatch.setenv("NOTIFY_SOCKET", str(notify_path))
            monkeypatch.setenv("WATCHDOG_USEC", "90000000")

            module._notify_systemd("READY=1\nSTATUS=local inference ready (desired)")
            ready = receiver.recv(1024)
            module._notify_systemd("WATCHDOG=1\nSTATUS=local inference healthy")
            watchdog = receiver.recv(1024)
    finally:
        notify_path.unlink(missing_ok=True)

    assert ready == b"READY=1\nSTATUS=local inference ready (desired)"
    assert watchdog == b"WATCHDOG=1\nSTATUS=local inference healthy"
    assert module._watchdog_interval() == 30


def test_supervisor_forwards_stop_immediately_without_a_final_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_supervisor()
    handlers: dict[int, Any] = {}
    notifications: list[str] = []

    class FakeProcess:
        pid = 4242
        terminated = False

        def poll(self):
            return -15 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, *, timeout: float):
            if notifications and not self.terminated:
                handler = handlers[signal.SIGTERM]
                handler(signal.SIGTERM, None)
                assert self.terminated, "SIGTERM was not forwarded to llama-server promptly"
            if self.terminated:
                return -15
            raise subprocess.TimeoutExpired("llama-server", timeout)

    process = FakeProcess()
    monkeypatch.setattr(
        module, "load_generation", lambda: SimpleNamespace(active_role="desired")
    )
    monkeypatch.setattr(module, "build_server_command", lambda _generation: ["server"])
    monkeypatch.setattr(module, "make_http_client", object)
    monkeypatch.setattr(module, "_ensure_listener_available", lambda: None)
    monkeypatch.setattr(module, "_listener_owned_by_pid", lambda _pid: True)
    monkeypatch.setattr(module, "probe_ready", lambda _opener: None)
    monkeypatch.setattr(
        module,
        "_probe_health_and_model",
        lambda _opener: pytest.fail("watchdog probe ran after stop request"),
    )
    monkeypatch.setattr(module, "_watchdog_interval", lambda: 30)
    monkeypatch.setattr(module, "_notify_systemd", notifications.append)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        module.signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )

    module.supervise()

    assert process.terminated
    assert notifications == ["READY=1\nSTATUS=local inference ready (desired)"]


def test_supervisor_validates_watchdog_before_publishing_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_supervisor()
    notifications: list[str] = []

    class FakeProcess:
        pid = 4242
        terminated = False

        def poll(self):
            return -15 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, *, timeout: float):
            if self.terminated:
                return -15
            raise subprocess.TimeoutExpired("llama-server", timeout)

    process = FakeProcess()
    monkeypatch.setattr(
        module, "load_generation", lambda: SimpleNamespace(active_role="desired")
    )
    monkeypatch.setattr(module, "build_server_command", lambda _generation: ["server"])
    monkeypatch.setattr(module, "make_http_client", object)
    monkeypatch.setattr(module, "_ensure_listener_available", lambda: None)
    monkeypatch.setattr(module, "_listener_owned_by_pid", lambda _pid: True)
    monkeypatch.setattr(module, "probe_ready", lambda _opener: None)
    monkeypatch.setattr(
        module,
        "_watchdog_interval",
        lambda: (_ for _ in ()).throw(module.InferenceServiceError("unsafe watchdog")),
    )
    monkeypatch.setattr(module, "_notify_systemd", notifications.append)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(module.InferenceServiceError):
        module.supervise()

    assert notifications == []
    assert process.terminated


def test_shutdown_cleanup_uses_only_the_remaining_signal_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_supervisor()
    waits: list[float] = []

    class IgnoringProcess:
        killed = False

        def poll(self):
            return None

        def terminate(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, *, timeout: float):
            waits.append(timeout)
            if self.killed:
                return -9
            raise subprocess.TimeoutExpired("llama-server", timeout)

    process = IgnoringProcess()
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)

    module._stop_process(process, shutdown_deadline=105.0)

    assert waits[0] == 5.0
    assert process.killed
