# Issue 88 — controlled production Matrix admin provisioning

GitHub: https://github.com/ZenithResearch/hub/issues/88

## Boundary

Provide a macOS operator-only script for creating explicit new Synapse administrator accounts through the production shared-secret registration endpoint. Public registration stays disabled. Matrix administrator status does not confer Hub authority. The browser, ZenithOS, appservices, and ordinary clients never receive the registration shared secret.

The existing `mgpi` and `banana` accounts are protected: validation rejects those localparts before AWS, HTTP, password generation, or Keychain activity.

## Implemented

- `scripts/provision_matrix_admins.py` fetches the raw production registration secret in-process from AWS Secrets Manager with boto3.
- The CLI requires one or more explicit usernames, defaults to the production HTTPS origin and secret handle, and offers a non-mutating `--preflight`.
- Unsafe endpoint shapes, invalid/duplicate/protected usernames, nonce failures, existing accounts, malformed responses, AWS failures, HTTP failures, and Keychain failures fail closed.
- Each account receives an independent 40-character CSPRNG temporary password.
- The temporary password reaches `/usr/bin/security` only over stdin and is stored after HTTP 200 registration under service `zenith-matrix-temporary-password` and account `<Matrix localpart>`.
- JSON output contains only username/user ID, status, and Keychain handle metadata.
- `docs/operations/matrix-admin-provisioning.md` documents prerequisites, preflight, invocation, first-login password change, retrieval/deletion, and verification.

## Verification

```bash
uv run --with pytest pytest -q tests/matrix/test_provision_matrix_admins.py
uv run --with pytest pytest -q tests/matrix
uv run --with ruff ruff check scripts/provision_matrix_admins.py tests/matrix
uv lock --check
python3 scripts/private_artifact_scan.py --range origin/main...HEAD
git diff --check
```

The production runtime contract remains `"enable_registration": False` in `infra/aws_baseline_80/matrix_synapse_runtime.tf`.

## Non-claims

This change does not provision an account by itself, rotate/reset existing accounts, expose any generated credential, enable public/self-registration, create appservice users, or make Matrix admin status a Hub authority role.
