"""Secret verification and bounded in-memory sessions for Hypha administration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

_MIN_SECRET_BYTES = 32
_MAX_SECRET_BYTES = 512
_TOKEN_BYTES = 32
_TOKEN_DIGEST_BYTES = 32


class AuthenticationRejected(RuntimeError):
    """Generic authentication/session rejection without secret-bearing context."""

    def __init__(self) -> None:
        super().__init__("administration authentication failed")


class RateLimited(RuntimeError):
    """A source exceeded the bounded authentication failure budget."""

    def __init__(self) -> None:
        super().__init__("administration authentication is temporarily unavailable")


class SessionCapacityExceeded(RuntimeError):
    """The process has reached its bounded live-session capacity."""

    def __init__(self) -> None:
        super().__init__("administration session capacity is unavailable")


@dataclass(frozen=True)
class SessionGrant:
    session_token: str = field(repr=False)
    absolute_expires_at: float
    expires_in_seconds: int
    idle_timeout_seconds: int


@dataclass
class _Session:
    issued_at: float
    idle_expires_at: float
    absolute_expires_at: float


@dataclass(frozen=True)
class _ScryptVerifier:
    n: int
    r: int
    p: int
    salt: bytes = field(repr=False)
    digest: bytes = field(repr=False)


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_decode(value: str) -> bytes:
    if not value or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in value):
        raise ValueError("invalid administration secret verifier")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid administration secret verifier") from exc


def _secret_bytes(secret: str) -> bytes:
    if not isinstance(secret, str):
        raise AuthenticationRejected()
    try:
        encoded = secret.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AuthenticationRejected() from exc
    if not _MIN_SECRET_BYTES <= len(encoded) <= _MAX_SECRET_BYTES:
        raise AuthenticationRejected()
    if any(byte < 0x20 or byte == 0x7F for byte in encoded):
        raise AuthenticationRejected()
    return encoded


def _parse_verifier(encoded: str) -> _ScryptVerifier:
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$")
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        salt = _urlsafe_decode(raw_salt)
        digest = _urlsafe_decode(raw_digest)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("invalid administration secret verifier") from exc
    if (
        algorithm != "scrypt"
        or n < 2**10
        or n > 2**20
        or n & (n - 1)
        or not 1 <= r <= 32
        or not 1 <= p <= 16
        or not 16 <= len(salt) <= 64
        or len(digest) != 32
    ):
        raise ValueError("invalid administration secret verifier")
    return _ScryptVerifier(n=n, r=r, p=p, salt=salt, digest=digest)


def encode_scrypt_verifier(
    secret: str,
    *,
    salt: bytes | None = None,
    n: int = 2**14,
    r: int = 8,
    p: int = 1,
) -> str:
    """Create the runtime verifier; callers must keep the raw secret elsewhere."""

    raw_secret = _secret_bytes(secret)
    selected_salt = salt if salt is not None else secrets.token_bytes(16)
    if not isinstance(selected_salt, bytes):
        raise ValueError("invalid administration secret verifier")
    probe = f"scrypt${n}${r}${p}${_urlsafe_encode(selected_salt)}$" + _urlsafe_encode(b"\x00" * 32)
    parsed = _parse_verifier(probe)
    digest = hashlib.scrypt(
        raw_secret,
        salt=parsed.salt,
        n=parsed.n,
        r=parsed.r,
        p=parsed.p,
        dklen=32,
    )
    return f"scrypt${n}${r}${p}${_urlsafe_encode(parsed.salt)}${_urlsafe_encode(digest)}"


class BrokerSessionStore:
    """Process-local, fail-closed administration sessions."""

    def __init__(
        self,
        *,
        verifier: str,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[int], bytes] = secrets.token_bytes,
        idle_timeout_seconds: int = 120,
        absolute_timeout_seconds: int = 600,
        max_sessions: int = 8,
        max_failures: int = 5,
        failure_window_seconds: int = 60,
    ) -> None:
        self._verifier = _parse_verifier(verifier)
        if (
            idle_timeout_seconds <= 0
            or absolute_timeout_seconds < idle_timeout_seconds
            or max_sessions <= 0
            or max_sessions > 64
            or max_failures <= 0
            or max_failures > 32
            or failure_window_seconds <= 0
            or failure_window_seconds > 3_600
        ):
            raise ValueError("invalid administration session policy")
        self._clock = clock
        self._token_factory = token_factory
        self._idle_timeout_seconds = idle_timeout_seconds
        self._absolute_timeout_seconds = absolute_timeout_seconds
        self._max_sessions = max_sessions
        self._max_failures = max_failures
        self._failure_window_seconds = failure_window_seconds
        self._sessions: dict[bytes, _Session] = {}
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def authenticate(self, secret: str, *, source: str) -> SessionGrant:
        now = self._clock()
        failures = self._bounded_failures(source, now)
        if len(failures) >= self._max_failures:
            raise RateLimited()
        try:
            raw_secret = _secret_bytes(secret)
        except AuthenticationRejected:
            failures.append(now)
            raise
        candidate = hashlib.scrypt(
            raw_secret,
            salt=self._verifier.salt,
            n=self._verifier.n,
            r=self._verifier.r,
            p=self._verifier.p,
            dklen=len(self._verifier.digest),
        )
        if not hmac.compare_digest(candidate, self._verifier.digest):
            failures.append(now)
            raise AuthenticationRejected()
        self._failures.pop(source, None)
        self._prune_sessions(now)
        if len(self._sessions) >= self._max_sessions:
            raise SessionCapacityExceeded()
        raw_token = self._token_factory(_TOKEN_BYTES)
        if not isinstance(raw_token, bytes) or len(raw_token) != _TOKEN_BYTES:
            raise SessionCapacityExceeded()
        token = _urlsafe_encode(raw_token)
        digest = self._token_digest(token)
        if digest in self._sessions:
            raise SessionCapacityExceeded()
        absolute_expires_at = now + self._absolute_timeout_seconds
        self._sessions[digest] = _Session(
            issued_at=now,
            idle_expires_at=now + self._idle_timeout_seconds,
            absolute_expires_at=absolute_expires_at,
        )
        return SessionGrant(
            session_token=token,
            absolute_expires_at=absolute_expires_at,
            expires_in_seconds=self._absolute_timeout_seconds,
            idle_timeout_seconds=self._idle_timeout_seconds,
        )

    def authorize(self, session_token: str) -> None:
        now = self._clock()
        digest = self._token_digest(session_token)
        session = self._sessions.get(digest)
        if session is None:
            raise AuthenticationRejected()
        if now >= session.idle_expires_at or now >= session.absolute_expires_at:
            self._sessions.pop(digest, None)
            raise AuthenticationRejected()
        session.idle_expires_at = min(
            now + self._idle_timeout_seconds,
            session.absolute_expires_at,
        )

    def logout(self, session_token: str) -> None:
        digest = self._token_digest(session_token)
        if self._sessions.pop(digest, None) is None:
            raise AuthenticationRejected()

    def _token_digest(self, token: str) -> bytes:
        if not isinstance(token, str) or not token or len(token) > 128:
            raise AuthenticationRejected()
        try:
            raw = token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise AuthenticationRejected() from exc
        digest = hashlib.sha256(raw).digest()
        if len(digest) != _TOKEN_DIGEST_BYTES:
            raise AuthenticationRejected()
        return digest

    def _bounded_failures(self, source: str, now: float) -> deque[float]:
        if (
            not isinstance(source, str)
            or not 1 <= len(source) <= 128
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in source)
        ):
            raise AuthenticationRejected()
        failures = self._failures[source]
        cutoff = now - self._failure_window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def _prune_sessions(self, now: float) -> None:
        expired = [
            digest
            for digest, session in self._sessions.items()
            if now >= session.idle_expires_at or now >= session.absolute_expires_at
        ]
        for digest in expired:
            self._sessions.pop(digest, None)
