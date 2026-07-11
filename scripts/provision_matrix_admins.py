#!/usr/bin/env python3
"""Provision production Synapse admins through the operator-only shared-secret flow."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import string
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

DEFAULT_ENDPOINT = "https://synapse.zenith-research.ca"
DEFAULT_REGION = "us-east-1"
DEFAULT_PROFILE = "zenith-hermes"
DEFAULT_SECRET_ID = "zenith-hub-prod/matrix/registration_shared_secret"
KEYCHAIN_SERVICE = "zenith-matrix-temporary-password"
PROTECTED_USERNAMES = frozenset({"banana", "mgpi"})
USERNAME_RE = re.compile(r"^[a-z0-9._=-]+$")
PASSWORD_LENGTH = 40

HttpClient = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]
SecretLoader = Callable[[], str]
KeychainStore = Callable[[str, str], None]
PasswordFactory = Callable[[], str]


class ProvisioningError(RuntimeError):
    """A fail-closed provisioning error whose message contains no secret material."""


def build_registration_mac(
    *, secret: str, nonce: str, username: str, credential: str, admin: bool
) -> str:
    role = "admin" if admin else "notadmin"
    message = "\x00".join((nonce, username, credential, role)).encode()
    return hmac.new(secret.encode(), message, hashlib.sha1).hexdigest()


def generate_temporary_password() -> str:
    rng = secrets.SystemRandom()
    required = [
        rng.choice(string.ascii_lowercase),
        rng.choice(string.ascii_uppercase),
        rng.choice(string.digits),
        rng.choice("-_"),
    ]
    alphabet = string.ascii_letters + string.digits + "-_"
    generated = required + [rng.choice(alphabet) for _ in range(PASSWORD_LENGTH - len(required))]
    rng.shuffle(generated)
    return "".join(generated)


def validate_usernames(usernames: Sequence[str]) -> list[str]:
    if not usernames:
        raise ProvisioningError("at least one explicit username is required")
    normalized: list[str] = []
    seen: set[str] = set()
    for username in usernames:
        if not USERNAME_RE.fullmatch(username):
            raise ProvisioningError(f"invalid Matrix username: {username!r}")
        if username in PROTECTED_USERNAMES:
            raise ProvisioningError(f"refusing to modify protected existing account: {username}")
        if username in seen:
            raise ProvisioningError(f"duplicate Matrix username: {username}")
        seen.add(username)
        normalized.append(username)
    return normalized


def validate_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ProvisioningError("Synapse endpoint must be an HTTPS origin without credentials")
    return endpoint.rstrip("/")


def _nonce(http: HttpClient, endpoint: str) -> str:
    status, payload = http("GET", f"{endpoint}/_synapse/admin/v1/register", None)
    if status != 200:
        raise ProvisioningError(f"nonce request failed with HTTP {status}")
    if not isinstance(payload, dict) or not isinstance(payload.get("nonce"), str):
        raise ProvisioningError("nonce response was invalid")
    nonce = payload["nonce"]
    if not nonce:
        raise ProvisioningError("nonce response was invalid")
    return nonce


def provision_admins(
    usernames: Sequence[str],
    *,
    secret_loader: SecretLoader,
    http: HttpClient,
    keychain_store: KeychainStore,
    password_factory: PasswordFactory = generate_temporary_password,
    endpoint: str = DEFAULT_ENDPOINT,
) -> list[dict[str, Any]]:
    checked_usernames = validate_usernames(usernames)
    endpoint = validate_endpoint(endpoint)
    secret = secret_loader()
    if not secret:
        raise ProvisioningError("AWS secret fetch returned an empty value")

    results: list[dict[str, Any]] = []
    for username in checked_usernames:
        nonce = _nonce(http, endpoint)
        temporary_credential = password_factory()
        payload = {
            "nonce": nonce,
            "username": username,
            "password": temporary_credential,
            "admin": True,
            "mac": build_registration_mac(
                secret=secret,
                nonce=nonce,
                username=username,
                credential=temporary_credential,
                admin=True,
            ),
        }
        status, response = http("POST", f"{endpoint}/_synapse/admin/v1/register", payload)
        errcode = response.get("errcode") if isinstance(response, dict) else None
        if status != 200:
            if errcode == "M_USER_IN_USE":
                raise ProvisioningError(f"account already exists: {username}")
            raise ProvisioningError(f"registration failed for {username} with HTTP {status}")
        user_id = response.get("user_id") if isinstance(response, dict) else None
        if not isinstance(user_id, str) or not user_id:
            raise ProvisioningError(f"registration response was invalid for {username}")

        keychain_store(username, temporary_credential)
        results.append(
            {
                "username": username,
                "user_id": user_id,
                "status": "provisioned",
                "keychain": {"service": KEYCHAIN_SERVICE, "account": username},
            }
        )
    return results


def preflight_admins(
    usernames: Sequence[str], *, secret_loader: SecretLoader, http: HttpClient, endpoint: str
) -> list[dict[str, Any]]:
    checked_usernames = validate_usernames(usernames)
    endpoint = validate_endpoint(endpoint)
    if not secret_loader():
        raise ProvisioningError("AWS secret fetch returned an empty value")
    results = []
    for username in checked_usernames:
        _nonce(http, endpoint)
        results.append(
            {
                "username": username,
                "status": "preflight_ready",
                "keychain": {"service": KEYCHAIN_SERVICE, "account": username},
            }
        )
    return results


def aws_secret_loader(*, secret_id: str, region: str, profile: str) -> SecretLoader:
    def load() -> str:
        try:
            import boto3

            session = boto3.Session(profile_name=profile or None, region_name=region)
            response = session.client("secretsmanager").get_secret_value(SecretId=secret_id)
            value = response.get("SecretString")
        except Exception as exc:
            raise ProvisioningError("AWS secret fetch failed") from exc
        if not isinstance(value, str) or not value:
            raise ProvisioningError("AWS secret fetch returned an empty value")
        return value

    return load


def http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"accept": "application/json", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            parsed: Any = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {}
        return exc.code, parsed
    except (OSError, TimeoutError) as exc:
        raise ProvisioningError("Synapse request failed") from exc


def store_in_keychain(account: str, password: str) -> None:
    try:
        process = subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                account,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            input=password + "\n",
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ProvisioningError("Keychain write failed") from exc
    if process.returncode != 0:
        raise ProvisioningError("Keychain write failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", nargs="+", help="explicit Matrix localpart(s) to provision")
    parser.add_argument("--preflight", action="store_true", help="check AWS and nonce access only")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--secret-id", default=DEFAULT_SECRET_ID)
    parser.add_argument("--aws-region", default=DEFAULT_REGION)
    parser.add_argument("--aws-profile", default=DEFAULT_PROFILE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    loader = aws_secret_loader(
        secret_id=args.secret_id,
        region=args.aws_region,
        profile=args.aws_profile,
    )
    try:
        if args.preflight:
            results = preflight_admins(
                args.username,
                secret_loader=loader,
                http=http_json,
                endpoint=args.endpoint.rstrip("/"),
            )
        else:
            results = provision_admins(
                args.username,
                secret_loader=loader,
                http=http_json,
                keychain_store=store_in_keychain,
                endpoint=args.endpoint.rstrip("/"),
            )
    except ProvisioningError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
