from __future__ import annotations

import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import stat
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra/hermes_cloud_agent/runtime/hermes-prepare-local-inference"
LOCK_SCHEMA = (
    ROOT / "infra/hermes_cloud_agent/artifacts/local-inference-lock.schema.json"
)


class FakeS3:
    def __init__(
        self,
        objects: dict[tuple[str, str, str], bytes],
        response_overrides: dict[tuple[str, str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.objects = objects
        self.response_overrides = response_overrides or {}
        self.calls: list[dict[str, str]] = []

    def get_object(self, **kwargs: str) -> dict[str, Any]:
        self.calls.append(kwargs)
        identity = (kwargs["Bucket"], kwargs["Key"], kwargs["VersionId"])
        payload = self.objects[identity]
        response = {
            "Body": io.BytesIO(payload),
            "ContentLength": len(payload),
            "VersionId": kwargs["VersionId"],
        }
        response.update(self.response_overrides.get(identity, {}))
        return response


def _load_preparer(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    fake_boto3 = SimpleNamespace(client=lambda _service: None)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    loader = importlib.machinery.SourceFileLoader("hermes_inference_prepare_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def _runtime_archive(
    *,
    unsafe_name: str | None = None,
    unsafe_type: str | None = None,
    library_payload: bytes = b"runtime-library",
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        entries = {
            unsafe_name or "llama-b10000/llama-server": b"#!/bin/sh\nexit 0\n",
            "llama-b10000/libllama.so": library_payload,
        }
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.mode = 0o755 if name.endswith("llama-server") else 0o644
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        if unsafe_type is not None:
            info = tarfile.TarInfo("llama-b10000/unsafe-member")
            if unsafe_type == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "llama-server"
            elif unsafe_type == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = "llama-b10000/llama-server"
            elif unsafe_type == "device":
                info.type = tarfile.CHRTYPE
                info.devmajor = 1
                info.devminor = 3
            else:
                raise AssertionError(f"unsupported test archive member: {unsafe_type}")
            archive.addfile(info)
    return buffer.getvalue()


def _lock(runtime: bytes, model: bytes) -> tuple[dict[str, Any], dict[tuple[str, str, str], bytes]]:
    runtime_sha = hashlib.sha256(runtime).hexdigest()
    model_sha = hashlib.sha256(model).hexdigest()
    bucket = "example-private-artifacts"
    runtime_key = f"runtime/{runtime_sha}/llama.tar.gz"
    model_key = f"models/{model_sha}/model.gguf"
    runtime_version = "runtime-version-1"
    model_version = "model-version-1"
    lock = {
        "schema_version": 1,
        "desired": {
            "llama_cpp": {
                "repository": "https://github.com/ggml-org/llama.cpp",
                "commit": "47a39665e7081dc482feec169961acc09750a5c4",
                "release": "b10000",
                "archive_filename": "llama-b10000-bin-ubuntu-x64.tar.gz",
                "archive_sha256": runtime_sha,
                "size_bytes": len(runtime),
                "s3_bucket": bucket,
                "s3_key": runtime_key,
                "s3_version_id": runtime_version,
            },
            "model": {
                "source_repository": "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
                "revision": "7c41481f57cb95916b40956ab2f0b139b296d974",
                "filename": "Qwen3-8B-Q4_K_M.gguf",
                "model_id": "qwen3-8b-q4-k-m",
                "sha256": model_sha,
                "size_bytes": len(model),
                "s3_bucket": bucket,
                "s3_key": model_key,
                "s3_version_id": model_version,
                "license": "apache-2.0",
                "context_length": 32768,
                "chat_template": "jinja",
                "tool_calling_verified": False,
            },
        },
    }
    return lock, {
        (bucket, runtime_key, runtime_version): runtime,
        (bucket, model_key, model_version): model,
    }


def _write_lock(tmp_path: Path, lock: dict[str, Any]) -> Path:
    lock_path = tmp_path / "local-inference.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return lock_path


def _prepare(
    module: ModuleType,
    tmp_path: Path,
    lock: dict[str, Any],
    s3: FakeS3,
) -> dict[str, Any]:
    return module.prepare_local_inference(
        lock_path=_write_lock(tmp_path, lock),
        schema_path=LOCK_SCHEMA,
        runtime_root=tmp_path / "runtime",
        model_root=tmp_path / "models",
        state_root=tmp_path / "state",
        s3_client=s3,
    )


def test_prepare_fetches_exact_versions_and_publishes_ready_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    model = b"valid-gguf-fixture"
    lock, objects = _lock(runtime, model)
    s3 = FakeS3(objects)

    ready = _prepare(module, tmp_path, lock, s3)

    assert s3.calls == [
        {
            "Bucket": lock["desired"]["llama_cpp"]["s3_bucket"],
            "Key": lock["desired"]["llama_cpp"]["s3_key"],
            "VersionId": lock["desired"]["llama_cpp"]["s3_version_id"],
        },
        {
            "Bucket": lock["desired"]["model"]["s3_bucket"],
            "Key": lock["desired"]["model"]["s3_key"],
            "VersionId": lock["desired"]["model"]["s3_version_id"],
        },
    ]
    runtime_dir = (
        tmp_path
        / "runtime"
        / lock["desired"]["llama_cpp"]["commit"]
        / lock["desired"]["llama_cpp"]["archive_sha256"]
    )
    server = runtime_dir / "llama-b10000" / "llama-server"
    assert server.read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert stat.S_IMODE(server.stat().st_mode) == 0o755
    model_path = tmp_path / "models" / f"{lock['desired']['model']['sha256']}.gguf"
    assert model_path.read_bytes() == model
    persisted = json.loads((tmp_path / "state" / "READY.json").read_text(encoding="utf-8"))
    assert persisted == ready
    assert ready["active_role"] == "desired"
    assert ready["generation_id"] == module.generation_id(lock["desired"])


def test_prepare_rejects_wrong_bytes_without_publishing_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    model = b"actual-model"
    lock, objects = _lock(runtime, model)
    lock["desired"]["model"]["sha256"] = "0" * 64

    with pytest.raises(module.PreparationError, match="artifact verification failed"):
        _prepare(module, tmp_path, lock, FakeS3(objects))

    assert not (tmp_path / "state" / "READY.json").exists()
    assert not list((tmp_path / "models").glob("*.gguf"))


def test_prepare_rejects_archive_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive(unsafe_name="../llama-server")
    lock, objects = _lock(runtime, b"model")

    with pytest.raises(module.PreparationError, match="unsafe runtime archive"):
        _prepare(module, tmp_path, lock, FakeS3(objects))

    assert not (tmp_path / "state" / "READY.json").exists()


@pytest.mark.parametrize("unsafe_type", ["symlink", "hardlink", "device"])
def test_prepare_rejects_non_regular_archive_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe_type: str
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive(unsafe_type=unsafe_type)
    lock, objects = _lock(runtime, b"model")

    with pytest.raises(module.PreparationError, match="unsafe runtime archive"):
        _prepare(module, tmp_path, lock, FakeS3(objects))

    assert not (tmp_path / "state" / "READY.json").exists()


@pytest.mark.parametrize("stream_shape", ["truncated", "oversized"])
def test_prepare_rejects_streams_that_disagree_with_declared_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream_shape: str
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    model = b"declared-model"
    lock, objects = _lock(runtime, model)
    model_artifact = lock["desired"]["model"]
    identity = (
        model_artifact["s3_bucket"],
        model_artifact["s3_key"],
        model_artifact["s3_version_id"],
    )
    objects[identity] = model[:-1] if stream_shape == "truncated" else model + b"x"
    overrides = {identity: {"ContentLength": len(model)}}

    with pytest.raises(module.PreparationError, match="artifact verification failed"):
        _prepare(module, tmp_path, lock, FakeS3(objects, overrides))

    assert not (tmp_path / "state" / "READY.json").exists()


def test_prepare_rejects_a_different_returned_s3_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    lock, objects = _lock(runtime, b"model")
    runtime_artifact = lock["desired"]["llama_cpp"]
    identity = (
        runtime_artifact["s3_bucket"],
        runtime_artifact["s3_key"],
        runtime_artifact["s3_version_id"],
    )
    body = io.BytesIO(objects[identity])

    with pytest.raises(module.PreparationError, match="artifact verification failed"):
        _prepare(
            module,
            tmp_path,
            lock,
            FakeS3(
                objects,
                {identity: {"VersionId": "different-version", "Body": body}},
            ),
        )

    assert body.closed
    assert not (tmp_path / "state" / "READY.json").exists()


def test_prepare_is_idempotent_without_redownloading_installed_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    lock, objects = _lock(runtime, b"model")
    first = _prepare(module, tmp_path, lock, FakeS3(objects))
    second_s3 = FakeS3({})

    second = _prepare(module, tmp_path, lock, second_s3)

    assert second == first
    assert second_s3.calls == []


def test_prepare_grants_the_inference_group_access_to_model_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    model = b"model"
    lock, objects = _lock(runtime, model)
    ownership_changes: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        module.grp, "getgrnam", lambda _name: SimpleNamespace(gr_gid=4242)
    )
    monkeypatch.setattr(
        module.os,
        "chown",
        lambda path, uid, gid: ownership_changes.append((Path(path), uid, gid)),
    )

    _prepare(module, tmp_path, lock, FakeS3(objects))

    model_root = tmp_path / "models"
    state_root = tmp_path / "state"
    assert stat.S_IMODE(model_root.stat().st_mode) == 0o750
    assert stat.S_IMODE(state_root.stat().st_mode) == 0o750
    assert (model_root, 0, 4242) in ownership_changes
    assert (state_root, 0, 4242) in ownership_changes
    assert any(
        path.parent == model_root
        and path.name.startswith("model-")
        and uid == 0
        and gid == 4242
        for path, uid, gid in ownership_changes
    )


def test_failed_update_selects_only_explicit_validated_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    model = b"rollback-model"
    accepted_lock, accepted_objects = _lock(runtime, model)
    first_ready = _prepare(module, tmp_path, accepted_lock, FakeS3(accepted_objects))

    update_lock = copy.deepcopy(accepted_lock)
    update_lock["rollback"] = copy.deepcopy(accepted_lock["desired"])
    update_lock["desired"]["model"]["sha256"] = "0" * 64
    update_lock["desired"]["model"]["s3_key"] = "models/bad/model.gguf"
    update_lock["desired"]["model"]["s3_version_id"] = "bad-version"
    update_objects = dict(accepted_objects)
    update_objects[("example-private-artifacts", "models/bad/model.gguf", "bad-version")] = model

    ready = _prepare(module, tmp_path, update_lock, FakeS3(update_objects))

    assert ready["active_role"] == "rollback"
    assert ready["generation_id"] == first_ready["generation_id"]
    assert ready["failed_desired_generation_id"] == module.generation_id(
        update_lock["desired"]
    )


def test_failed_update_preserves_rollback_runtime_with_same_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    accepted_runtime = _runtime_archive(library_payload=b"accepted-runtime")
    desired_runtime = _runtime_archive(library_payload=b"new-runtime")
    model = b"rollback-model"
    accepted_lock, accepted_objects = _lock(accepted_runtime, model)
    accepted_ready = _prepare(
        module, tmp_path, accepted_lock, FakeS3(accepted_objects)
    )

    update_lock, update_objects = _lock(desired_runtime, model)
    update_lock["rollback"] = copy.deepcopy(accepted_lock["desired"])
    update_lock["desired"]["model"]["sha256"] = "0" * 64
    update_lock["desired"]["model"]["s3_key"] = "models/bad/model.gguf"
    update_lock["desired"]["model"]["s3_version_id"] = "bad-version"
    update_objects.update(accepted_objects)
    update_objects[("example-private-artifacts", "models/bad/model.gguf", "bad-version")] = model

    ready = _prepare(module, tmp_path, update_lock, FakeS3(update_objects))

    assert ready["active_role"] == "rollback"
    assert ready["generation_id"] == accepted_ready["generation_id"]
    accepted_runtime_path = (
        tmp_path
        / "runtime"
        / accepted_lock["desired"]["llama_cpp"]["commit"]
        / accepted_lock["desired"]["llama_cpp"]["archive_sha256"]
        / "llama-b10000"
        / "llama-server"
    )
    assert accepted_runtime_path.is_file()


def test_rollback_rejects_post_install_runtime_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    model = b"accepted-model"
    accepted_lock, accepted_objects = _lock(runtime, model)
    _prepare(module, tmp_path, accepted_lock, FakeS3(accepted_objects))
    runtime_root = (
        tmp_path
        / "runtime"
        / accepted_lock["desired"]["llama_cpp"]["commit"]
        / accepted_lock["desired"]["llama_cpp"]["archive_sha256"]
    )
    (runtime_root / "llama-b10000" / "libllama.so").write_bytes(b"corrupt")

    update_lock = copy.deepcopy(accepted_lock)
    update_lock["rollback"] = copy.deepcopy(accepted_lock["desired"])
    update_lock["desired"]["model"]["sha256"] = "0" * 64
    model_artifact = accepted_lock["desired"]["model"]
    model_identity = (
        model_artifact["s3_bucket"],
        model_artifact["s3_key"],
        model_artifact["s3_version_id"],
    )

    with pytest.raises(module.PreparationError, match="no valid declared rollback"):
        _prepare(
            module,
            tmp_path,
            update_lock,
            FakeS3({model_identity: accepted_objects[model_identity]}),
        )


def test_interrupted_runtime_promotion_keeps_the_previous_generation_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    accepted_runtime = _runtime_archive(library_payload=b"accepted-runtime")
    desired_runtime = _runtime_archive(library_payload=b"desired-runtime")
    model = b"accepted-model"
    accepted_lock, accepted_objects = _lock(accepted_runtime, model)
    accepted_ready = _prepare(
        module, tmp_path, accepted_lock, FakeS3(accepted_objects)
    )
    update_lock, update_objects = _lock(desired_runtime, model)
    update_lock["rollback"] = copy.deepcopy(accepted_lock["desired"])
    update_objects.update(accepted_objects)
    desired_target = (
        tmp_path
        / "runtime"
        / update_lock["desired"]["llama_cpp"]["commit"]
        / update_lock["desired"]["llama_cpp"]["archive_sha256"]
    )
    real_replace = module.os.replace

    def interrupt_runtime(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        if Path(destination) == desired_target and ".prepare-" in source_path.name:
            raise OSError("simulated interrupted runtime promotion")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", interrupt_runtime)

    ready = _prepare(module, tmp_path, update_lock, FakeS3(update_objects))

    assert ready["active_role"] == "rollback"
    assert ready["generation_id"] == accepted_ready["generation_id"]
    assert not desired_target.exists()


def test_interrupted_model_promotion_keeps_the_previous_generation_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    accepted_model = b"accepted-model"
    desired_model = b"desired-model"
    accepted_lock, accepted_objects = _lock(runtime, accepted_model)
    accepted_ready = _prepare(
        module, tmp_path, accepted_lock, FakeS3(accepted_objects)
    )
    update_lock, update_objects = _lock(runtime, desired_model)
    update_lock["rollback"] = copy.deepcopy(accepted_lock["desired"])
    update_objects.update(accepted_objects)
    desired_target = (
        tmp_path
        / "models"
        / f"{update_lock['desired']['model']['sha256']}.gguf"
    )
    real_replace = module.os.replace

    def interrupt_model(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        if Path(destination) == desired_target and source_path.name.startswith("model-"):
            raise OSError("simulated interrupted model promotion")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", interrupt_model)

    ready = _prepare(module, tmp_path, update_lock, FakeS3(update_objects))

    assert ready["active_role"] == "rollback"
    assert ready["generation_id"] == accepted_ready["generation_id"]
    assert not desired_target.exists()


@pytest.mark.parametrize("drift_part", ["commit_root", "target", "runtime_tree"])
def test_rollback_rejects_runtime_directory_permission_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift_part: str
) -> None:
    module = _load_preparer(monkeypatch)
    accepted_runtime = _runtime_archive(library_payload=b"accepted-runtime")
    desired_runtime = _runtime_archive(library_payload=b"desired-runtime")
    model = b"accepted-model"
    accepted_lock, accepted_objects = _lock(accepted_runtime, model)
    _prepare(module, tmp_path, accepted_lock, FakeS3(accepted_objects))
    commit_root = (
        tmp_path / "runtime" / accepted_lock["desired"]["llama_cpp"]["commit"]
    )
    target = commit_root / accepted_lock["desired"]["llama_cpp"]["archive_sha256"]
    paths = {
        "commit_root": commit_root,
        "target": target,
        "runtime_tree": target / "llama-b10000",
    }
    drift_path = paths[drift_part]
    assert stat.S_IMODE(drift_path.stat().st_mode) == 0o755
    drift_path.chmod(0o700)
    update_lock, _ = _lock(desired_runtime, model)
    update_lock["rollback"] = copy.deepcopy(accepted_lock["desired"])

    with pytest.raises(module.PreparationError, match="no valid declared rollback"):
        _prepare(module, tmp_path, update_lock, FakeS3({}))


def test_failed_update_without_declared_rollback_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    model = b"accepted-model"
    accepted_lock, accepted_objects = _lock(runtime, model)
    first_ready = _prepare(module, tmp_path, accepted_lock, FakeS3(accepted_objects))

    bad_lock = copy.deepcopy(accepted_lock)
    bad_lock["desired"]["model"]["sha256"] = "0" * 64

    with pytest.raises(module.PreparationError, match="no valid declared rollback"):
        _prepare(module, tmp_path, bad_lock, FakeS3(accepted_objects))

    assert json.loads((tmp_path / "state" / "READY.json").read_text()) == first_ready


def test_rollback_rejects_incomplete_ready_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_preparer(monkeypatch)
    runtime = _runtime_archive()
    model = b"accepted-model"
    accepted_lock, accepted_objects = _lock(runtime, model)
    first_ready = _prepare(module, tmp_path, accepted_lock, FakeS3(accepted_objects))
    (tmp_path / "state" / "READY.json").write_text(
        json.dumps({"generation_id": first_ready["generation_id"]}), encoding="utf-8"
    )

    update_lock = copy.deepcopy(accepted_lock)
    update_lock["rollback"] = copy.deepcopy(accepted_lock["desired"])
    update_lock["desired"]["model"]["sha256"] = "0" * 64

    with pytest.raises(module.PreparationError, match="no valid declared rollback"):
        _prepare(module, tmp_path, update_lock, FakeS3(accepted_objects))
