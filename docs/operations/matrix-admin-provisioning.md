# Controlled Matrix administrator provisioning

This runbook is for a Hub operator creating a new production Synapse administrator. It uses the production registration shared secret in-process, stores each generated temporary password in the operator's macOS Keychain, and prints only non-secret result metadata. Matrix administrator status does not grant Hub authority, and public registration remains disabled.

The script refuses the protected existing `mgpi` and `banana` accounts before loading the AWS secret or contacting Synapse. It does not update, reset, or otherwise touch those accounts.

## Prerequisites

- Run from a trusted macOS operator workstation with `/usr/bin/security` available.
- Authenticate the `zenith-hermes` AWS profile for `us-east-1` and confirm it can read `zenith-hub-prod/matrix/registration_shared_secret`.
- Confirm `https://synapse.zenith-research.ca/_matrix/client/versions` is healthy.
- Choose only new, explicit Matrix localparts. Never pass a password, shared secret, HMAC, or access token on the command line.
- Keep shell tracing disabled. Do not redirect terminal output into tickets, CI logs, chat, or PR text.

## Preflight

Preflight checks username validation, AWS secret access, and the Synapse registration nonce endpoint. It does not generate a password, register an account, or write Keychain data.

```bash
set +x
uv run python scripts/provision_matrix_admins.py --preflight new-operator
```

Review only the returned username, `preflight_ready` status, and Keychain handle. A failure is terminal; fix the prerequisite and rerun preflight rather than bypassing it.

## Provision

Provision only after preflight succeeds:

```bash
set +x
uv run python scripts/provision_matrix_admins.py new-operator
```

Multiple new localparts may be supplied explicitly. For each account, the script obtains a fresh nonce, generates an independent 40-character CSPRNG temporary password, registers an admin through `/_synapse/admin/v1/register`, and writes the password to Keychain only after Synapse returns HTTP 200. Existing-account, nonce, AWS, HTTP, invalid-response, and Keychain failures return a nonzero exit and never report success.

The Keychain pair is:

- service: `zenith-matrix-temporary-password`
- account: the exact Matrix localpart

If Synapse registration succeeds but the Keychain write fails, stop. The account may exist without a recoverable operator password; do not retry as though registration were idempotently successful. Use an approved Synapse password-reset procedure.

## Retrieve, change, and delete the temporary password

Retrieve the temporary password only in a private interactive terminal immediately before first login:

```bash
security find-generic-password -a new-operator -s zenith-matrix-temporary-password -w
```

Paste it directly into an approved Matrix client. Do not place it in argv, shell history, scripts, screenshots, test output, or support messages. Treat the first login as a forced-change expectation: change the temporary password immediately using the client's account-security settings, then verify a fresh login with the replacement password.

After the password change and login verification, delete the temporary Keychain item:

```bash
security delete-generic-password -a new-operator -s zenith-matrix-temporary-password
```

Confirm deletion by rerunning the `security find-generic-password` command and expecting a not-found result.

## Verify the account

1. Confirm the returned user ID is `@<localpart>:synapse.zenith-research.ca`.
2. Log in through an approved Matrix client with the temporary password.
3. Change the password immediately and verify a fresh client login.
4. Through an already-authorized operator tool that keeps its access token out of argv and logs, inspect `/_synapse/admin/v2/users/@<localpart>:synapse.zenith-research.ca` and confirm `admin: true`.
5. Confirm public/self-registration still rejects unauthenticated registration; do not enable it for this workflow.
6. Delete the temporary Keychain item after the password change is proven.

Never paste an administrator access token into a command line merely to perform step 4. This script does not accept, retrieve, or print Matrix access tokens.
