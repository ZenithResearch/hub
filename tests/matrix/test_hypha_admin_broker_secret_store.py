from __future__ import annotations

import os

import pytest

from services.hypha_admin_broker.auth import (
    AuthenticationRejected,
    BrokerSessionStore,
    encode_scrypt_verifier,
)
from services.hypha_admin_broker.secret_store import (
    AtomicFileSecretVerifierStore,
    SecretVerifierStoreError,
)

OLD_SECRET = "correct-administration-secret-value-1234"
NEW_SECRET = "replacement-administration-secret-value-5678"


def verifier(secret: str, salt: bytes) -> str:
    return encode_scrypt_verifier(secret, salt=salt, n=2**10, r=8, p=1)


def test_file_store_seeds_rotates_and_restores_without_persisting_raw_secret(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "operator-secret.verifier"
    initial = verifier(OLD_SECRET, b"0123456789abcdef")
    replacement = verifier(NEW_SECRET, b"fedcba9876543210")
    store = AtomicFileSecretVerifierStore(str(path))

    assert store.load_or_initialize(initial) == initial
    assert path.stat().st_mode & 0o777 == 0o600
    assert OLD_SECRET not in path.read_text(encoding="ascii")

    store.replace(replacement)

    assert NEW_SECRET not in path.read_text(encoding="ascii")
    restarted = AtomicFileSecretVerifierStore(str(path))
    restored = restarted.load_or_initialize(initial)
    assert restored == replacement
    sessions = BrokerSessionStore(verifier=restored)
    with pytest.raises(AuthenticationRejected):
        sessions.authenticate(OLD_SECRET, source="test-old")
    assert sessions.authenticate(NEW_SECRET, source="test-new").session_token


def test_file_store_rejects_symlinked_or_group_writable_state(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    initial = verifier(OLD_SECRET, b"0123456789abcdef")

    with pytest.raises(SecretVerifierStoreError):
        AtomicFileSecretVerifierStore(str(linked / "operator-secret.verifier")).load_or_initialize(
            initial
        )

    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o700)
    os.chmod(insecure, 0o770)
    with pytest.raises(SecretVerifierStoreError):
        AtomicFileSecretVerifierStore(
            str(insecure / "operator-secret.verifier")
        ).load_or_initialize(initial)
