from __future__ import annotations

import base64
import hashlib

import pytest

from services.hypha_admin_broker.auth import (
    AuthenticationRejected,
    BrokerSessionStore,
    RateLimited,
    SessionCapacityExceeded,
    encode_scrypt_verifier,
)


class Clock:
    def __init__(self, now: float = 1_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Tokens:
    def __init__(self):
        self.values = [bytes([index]) * 32 for index in range(1, 32)]

    def __call__(self, size: int) -> bytes:
        assert size == 32
        return self.values.pop(0)


def make_store(
    *,
    secret: str = "correct-administration-secret-value-1234",
    clock: Clock | None = None,
    tokens: Tokens | None = None,
    max_sessions: int = 4,
    max_failures: int = 3,
) -> BrokerSessionStore:
    verifier = encode_scrypt_verifier(secret, salt=b"0123456789abcdef", n=2**10, r=8, p=1)
    return BrokerSessionStore(
        verifier=verifier,
        clock=clock or Clock(),
        token_factory=tokens or Tokens(),
        idle_timeout_seconds=120,
        absolute_timeout_seconds=600,
        max_sessions=max_sessions,
        max_failures=max_failures,
        failure_window_seconds=60,
    )


def test_valid_secret_issues_256_bit_opaque_token_and_safe_grant_repr():
    store = make_store()

    grant = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")

    decoded = base64.urlsafe_b64decode(grant.session_token + "==")
    assert len(decoded) == 32
    assert grant.idle_timeout_seconds == 120
    assert grant.absolute_expires_at == 1_600.0
    assert grant.session_token not in repr(grant)


def test_store_indexes_sessions_by_digest_and_never_exposes_raw_token_in_repr():
    store = make_store()
    grant = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")

    expected_digest = hashlib.sha256(grant.session_token.encode("ascii")).digest()
    assert expected_digest in store._sessions
    assert grant.session_token not in repr(store)
    assert "correct-administration-secret-value-1234" not in repr(store)


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "short",
        "contains-control-character-123456\n",
        "x" * 513,
    ],
)
def test_malformed_secrets_fail_with_the_same_safe_authentication_error(secret):
    store = make_store()

    with pytest.raises(AuthenticationRejected) as captured:
        store.authenticate(secret, source="192.0.2.1")

    assert str(captured.value) == "administration authentication failed"
    if secret:
        assert secret not in repr(captured.value)


def test_wrong_secret_fails_without_leaking_secret_or_verifier():
    store = make_store()
    wrong = "wrong-administration-secret-value-5678"

    with pytest.raises(AuthenticationRejected) as captured:
        store.authenticate(wrong, source="192.0.2.1")

    assert str(captured.value) == "administration authentication failed"
    assert wrong not in repr(captured.value)
    assert "scrypt$" not in repr(captured.value)


def test_authorized_use_advances_only_idle_deadline():
    clock = Clock()
    store = make_store(clock=clock)
    grant = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")
    digest = hashlib.sha256(grant.session_token.encode("ascii")).digest()
    original_absolute = store._sessions[digest].absolute_expires_at

    clock.now += 100
    store.authorize(grant.session_token)

    assert store._sessions[digest].idle_expires_at == 1_220.0
    assert store._sessions[digest].absolute_expires_at == original_absolute


def test_idle_and_absolute_deadlines_expire_before_authority_is_returned():
    clock = Clock()
    store = make_store(clock=clock, tokens=Tokens())
    idle = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")
    clock.now = 1_120.0
    with pytest.raises(AuthenticationRejected):
        store.authorize(idle.session_token)

    clock.now = 2_000.0
    absolute = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")
    for _ in range(5):
        clock.now += 100
        store.authorize(absolute.session_token)
    clock.now = 2_600.0
    with pytest.raises(AuthenticationRejected):
        store.authorize(absolute.session_token)


def test_logout_and_process_restart_revoke_sessions():
    store = make_store()
    grant = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")
    store.logout(grant.session_token)
    with pytest.raises(AuthenticationRejected):
        store.authorize(grant.session_token)

    first_process = make_store(tokens=Tokens())
    prior = first_process.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")
    restarted_process = make_store(tokens=Tokens())
    with pytest.raises(AuthenticationRejected):
        restarted_process.authorize(prior.session_token)


def test_expired_sessions_are_garbage_collected_before_capacity_is_enforced():
    clock = Clock()
    store = make_store(clock=clock, tokens=Tokens(), max_sessions=1)
    first = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")
    with pytest.raises(SessionCapacityExceeded):
        store.authenticate("correct-administration-secret-value-1234", source="192.0.2.2")

    clock.now += 121
    second = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.2")
    assert second.session_token != first.session_token


def test_failed_authentication_is_rate_limited_per_bounded_source():
    clock = Clock()
    store = make_store(clock=clock, max_failures=2)
    for _ in range(2):
        with pytest.raises(AuthenticationRejected):
            store.authenticate("wrong-administration-secret-value-5678", source="192.0.2.1")

    with pytest.raises(RateLimited):
        store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")

    other = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.2")
    assert other.session_token
    clock.now += 61
    recovered = store.authenticate("correct-administration-secret-value-1234", source="192.0.2.1")
    assert recovered.session_token


def test_invalid_verifier_configuration_fails_closed_without_accepting_any_secret():
    with pytest.raises(ValueError, match="invalid administration secret verifier"):
        BrokerSessionStore(verifier="not-a-verifier")


def test_rotation_replaces_authority_and_revokes_every_existing_session():
    old_secret = "correct-administration-secret-value-1234"
    new_secret = "replacement-administration-secret-value-5678"
    store = make_store(secret=old_secret, tokens=Tokens())
    prior = store.authenticate(old_secret, source="192.0.2.1")
    replacement = encode_scrypt_verifier(
        new_secret,
        salt=b"fedcba9876543210",
        n=2**10,
        r=8,
        p=1,
    )

    store.rotate(replacement)

    with pytest.raises(AuthenticationRejected):
        store.authorize(prior.session_token)
    with pytest.raises(AuthenticationRejected):
        store.authenticate(old_secret, source="192.0.2.1")
    grant = store.authenticate(new_secret, source="192.0.2.2")
    assert grant.session_token
