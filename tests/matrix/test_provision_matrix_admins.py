import hashlib
import hmac
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "provision_matrix_admins.py"
RUNBOOK = ROOT / "docs" / "operations" / "matrix-admin-provisioning.md"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.provision_matrix_admins import (  # noqa: E402
    KEYCHAIN_SERVICE,
    PROTECTED_USERNAMES,
    ProvisioningError,
    build_registration_mac,
    generate_temporary_password,
    preflight_admins,
    provision_admins,
    store_in_keychain,
)


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, payload=None):
        self.calls.append((method, url, payload))
        return self.responses.pop(0)


class FakeKeychain:
    def __init__(self, *, fail=False, events=None):
        self.fail = fail
        self.events = events if events is not None else []
        self.items = []

    def __call__(self, account, password):
        self.events.append(("keychain", account))
        if self.fail:
            raise ProvisioningError("keychain write failed")
        self.items.append((account, password))


def test_hmac_matches_synapse_shared_secret_admin_registration_contract():
    actual = build_registration_mac(
        secret="shared-value",
        nonce="nonce-value",
        username="operator",
        password="<password>",
        admin=True,
    )

    expected_message = b"nonce-value\x00operator\x00<password>\x00admin"
    expected = hmac.new(b"shared-value", expected_message, hashlib.sha1).hexdigest()
    assert actual == expected


def test_generated_passwords_are_independent_and_meet_policy():
    passwords = {generate_temporary_password() for _ in range(32)}

    assert len(passwords) == 32
    assert all(len(password) >= 32 for password in passwords)
    assert all(any(char.islower() for char in password) for password in passwords)
    assert all(any(char.isupper() for char in password) for password in passwords)
    assert all(any(char.isdigit() for char in password) for password in passwords)
    assert all(any(char in "-_" for char in password) for password in passwords)


def test_registration_saves_keychain_only_after_http_200_and_returns_safe_metadata():
    events = []

    def http(method, url, payload=None):
        events.append(("http", method))
        if method == "GET":
            return 200, {"nonce": "server-nonce"}
        return 200, {"user_id": "@new-admin:synapse.zenith-research.ca"}

    keychain = FakeKeychain(events=events)
    results = provision_admins(
        ["new-admin"],
        secret_loader=lambda: "registration-secret-value",
        http=http,
        keychain_store=keychain,
        password_factory=lambda: "Aa1_" + "x" * 30,
    )

    assert events == [("http", "GET"), ("http", "POST"), ("keychain", "new-admin")]
    assert results == [
        {
            "username": "new-admin",
            "user_id": "@new-admin:synapse.zenith-research.ca",
            "status": "provisioned",
            "keychain": {"service": KEYCHAIN_SERVICE, "account": "new-admin"},
        }
    ]
    rendered = json.dumps(results)
    assert "registration-secret-value" not in rendered
    assert "Aa1_" not in rendered
    assert "server-nonce" not in rendered
    assert "mac" not in rendered.lower()


def test_preflight_returns_safe_metadata_without_generating_or_storing_passwords():
    http = FakeHttp([(200, {"nonce": "server-nonce"})])

    results = preflight_admins(
        ["new-admin"],
        secret_loader=lambda: "registration-secret-value",
        http=http,
        endpoint="https://synapse.zenith-research.ca",
    )

    assert results == [
        {
            "username": "new-admin",
            "status": "preflight_ready",
            "keychain": {"service": KEYCHAIN_SERVICE, "account": "new-admin"},
        }
    ]
    assert http.calls == [
        ("GET", "https://synapse.zenith-research.ca/_synapse/admin/v1/register", None)
    ]
    rendered = json.dumps(results)
    assert "registration-secret-value" not in rendered
    assert "server-nonce" not in rendered


@pytest.mark.parametrize("username", sorted(PROTECTED_USERNAMES))
def test_existing_operator_accounts_are_rejected_before_secret_or_network_access(username):
    calls = []

    with pytest.raises(ProvisioningError, match="protected existing account"):
        provision_admins(
            [username],
            secret_loader=lambda: calls.append("secret") or "registration-secret-value",
            http=lambda *args: calls.append("http"),
            keychain_store=lambda *args: calls.append("keychain"),
        )

    assert calls == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://synapse.zenith-research.ca",
        "https://operator@example.test",
        "https://example.test/path",
        "https://example.test?target=other",
        "https://example.test#fragment",
    ],
)
def test_unsafe_endpoint_is_rejected_before_secret_or_network_access(endpoint):
    calls = []

    with pytest.raises(ProvisioningError, match="HTTPS origin"):
        provision_admins(
            ["new-admin"],
            secret_loader=lambda: calls.append("secret") or "registration-secret-value",
            http=lambda *args: calls.append("http"),
            keychain_store=lambda *args: calls.append("keychain"),
            endpoint=endpoint,
        )

    assert calls == []


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        ([(503, {})], "nonce request failed"),
        ([(200, {})], "nonce response was invalid"),
        ([(200, {"nonce": "n"}), (400, {"errcode": "M_USER_IN_USE"})], "already exists"),
        ([(200, {"nonce": "n"}), (500, {})], "registration failed"),
    ],
)
def test_nonce_and_registration_failures_are_closed_without_keychain_write(responses, message):
    keychain = FakeKeychain()

    with pytest.raises(ProvisioningError, match=message):
        provision_admins(
            ["new-admin"],
            secret_loader=lambda: "registration-secret-value",
            http=FakeHttp(responses),
            keychain_store=keychain,
            password_factory=lambda: "Aa1_" + "x" * 30,
        )

    assert keychain.items == []


def test_aws_and_keychain_failures_do_not_report_success():
    def aws_failure():
        raise ProvisioningError("AWS secret fetch failed")

    with pytest.raises(ProvisioningError, match="AWS secret fetch failed"):
        provision_admins(
            ["new-admin"],
            secret_loader=aws_failure,
            http=FakeHttp([]),
            keychain_store=FakeKeychain(),
        )

    with pytest.raises(ProvisioningError, match="keychain write failed"):
        provision_admins(
            ["new-admin"],
            secret_loader=lambda: "registration-secret-value",
            http=FakeHttp([(200, {"nonce": "n"}), (200, {"user_id": "@new-admin:server"})]),
            keychain_store=FakeKeychain(fail=True),
            password_factory=lambda: "Aa1_" + "x" * 30,
        )


def test_cli_requires_explicit_usernames_and_never_exposes_secret_options():
    help_result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    missing_result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert help_result.returncode == 0
    assert "--secret-value" not in help_result.stdout
    assert "username [username ...]" in help_result.stdout
    assert missing_result.returncode != 0
    assert "username" in missing_result.stderr.lower()


def test_keychain_password_is_sent_over_stdin_and_never_process_arguments(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    store_in_keychain("new-admin", "generated-password-value")

    assert "generated-password-value" not in captured["argv"]
    assert captured["input"] == "generated-password-value\n"


def test_operator_runbook_documents_safe_account_lifecycle_and_authority_boundaries():
    runbook = RUNBOOK.read_text()

    for required in [
        "--preflight",
        "security find-generic-password",
        "security delete-generic-password",
        "zenith-matrix-temporary-password",
        "temporary password",
        "change",
        "/_synapse/admin/v2/users/",
        "public registration remains disabled",
        "does not grant Hub authority",
        "mgpi",
        "banana",
    ]:
        assert required in runbook


def test_production_synapse_keeps_public_registration_disabled():
    runtime = (ROOT / "infra" / "aws_baseline_80" / "matrix_synapse_runtime.tf").read_text()

    assert '"enable_registration": False' in runtime
