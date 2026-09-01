"""Durable verifier-only storage for broker operator-secret rotation."""

from __future__ import annotations

import os
import secrets
import stat
import threading
from pathlib import Path

from .auth import validate_scrypt_verifier

_MAX_VERIFIER_BYTES = 2_048


class SecretVerifierStoreError(RuntimeError):
    """A generic storage failure that never contains verifier or secret material."""

    def __init__(self) -> None:
        super().__init__("administration secret rotation is unavailable")


class AtomicFileSecretVerifierStore:
    """Persist one scrypt verifier through a locked, atomic file replacement."""

    def __init__(self, path: str) -> None:
        candidate = Path(path)
        if not candidate.is_absolute() or not candidate.name or len(str(candidate)) > 1_024:
            raise SecretVerifierStoreError()
        self._path = candidate
        self._lock = threading.RLock()

    def load_or_initialize(self, initial_verifier: str) -> str:
        try:
            validate_scrypt_verifier(initial_verifier)
        except (TypeError, ValueError) as exc:
            raise SecretVerifierStoreError() from exc
        with self._lock:
            try:
                return self._read()
            except FileNotFoundError:
                try:
                    self._replace(initial_verifier, require_absent=True)
                    return initial_verifier
                except FileExistsError:
                    return self._read()
            except SecretVerifierStoreError:
                raise
            except OSError as exc:
                raise SecretVerifierStoreError() from exc

    def replace(self, verifier: str) -> None:
        try:
            validate_scrypt_verifier(verifier)
        except (TypeError, ValueError) as exc:
            raise SecretVerifierStoreError() from exc
        with self._lock:
            self._replace(verifier, require_absent=False)

    def _parent_fd(self) -> int:
        parent = self._path.parent
        try:
            if parent.resolve(strict=True) != parent:
                raise SecretVerifierStoreError()
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            metadata = os.fstat(descriptor)
        except SecretVerifierStoreError:
            raise
        except OSError as exc:
            raise SecretVerifierStoreError() from exc
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            os.close(descriptor)
            raise SecretVerifierStoreError()
        return descriptor

    def _read(self) -> str:
        parent_fd = self._parent_fd()
        try:
            descriptor = os.open(self._path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
                    or metadata.st_size <= 0
                    or metadata.st_size > _MAX_VERIFIER_BYTES
                ):
                    raise SecretVerifierStoreError()
                raw = os.read(descriptor, _MAX_VERIFIER_BYTES + 1)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_fd)
        if len(raw) > _MAX_VERIFIER_BYTES:
            raise SecretVerifierStoreError()
        try:
            verifier = raw.decode("ascii")
            validate_scrypt_verifier(verifier)
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise SecretVerifierStoreError() from exc
        return verifier

    def _replace(self, verifier: str, *, require_absent: bool) -> None:
        encoded = verifier.encode("ascii")
        if not encoded or len(encoded) > _MAX_VERIFIER_BYTES:
            raise SecretVerifierStoreError()
        parent_fd = self._parent_fd()
        temporary_name = f".{self._path.name}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        try:
            if require_absent:
                try:
                    probe = os.open(self._path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                else:
                    os.close(probe)
                    raise FileExistsError(self._path.name)
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            written = 0
            while written < len(encoded):
                written += os.write(descriptor, encoded[written:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(
                temporary_name,
                self._path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        except FileExistsError:
            raise
        except (OSError, SecretVerifierStoreError) as exc:
            raise SecretVerifierStoreError() from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(parent_fd)
