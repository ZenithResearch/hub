# Matrix MSC4108 and MAS production rollout

This runbook introduces Matrix Authentication Service (MAS) so Hypha can use MSC4108 QR login. It is a phased stateful migration, not an ordinary rolling deployment. The real import requires a maintenance window, requires the operator to stop Synapse, and is not easily reversible.

## Safety boundaries

- `enable_matrix_mas` provisions inactive infrastructure only.
- `enable_matrix_mas_public_edge` attaches the certificate, auth-host rule, and desired-zero ECS service only after external DNS validation has issued the certificate.
- `start_matrix_mas_service` starts MAS for pre-cutover checks.
- `matrix_mas_cutover_complete` changes Synapse authentication and compatibility routing. Set it only after a successful stopped-Synapse import.
- Never place MAS database passwords, the Synapse/MAS shared secret, the MAS encryption secret, or the MAS signing key in Terraform variables, plans, command arguments, logs, or evidence files.
- Populate the three MAS Secrets Manager handles out of band. The encryption secret is immutable after first use.
- Keep `/health`, `/metrics`, and `/api/admin/v1/*` private.
- Preserve the known-good Synapse task revision and coordinated RDS/EFS backups before migration.

## Phase 1: inactive infrastructure

1. Build `infra/matrix/mas/Dockerfile`, scan it, mirror the reviewed image into the production AWS account, and pin its digest in `matrix_mas_image`.
2. Plan with `enable_matrix_mas=true`, `start_matrix_mas_service=false`, and `matrix_mas_cutover_complete=false`.
3. Reject a plan that replaces or destroys the existing Synapse RDS, EFS, ALB, listener, or service.
4. Apply only after review. This creates the dedicated private MAS RDS, secret handles, certificate request, target group, logs, task definition, and service discovery without attaching an unissued certificate.
5. Populate the MAS shared secret, 64-character hex encryption secret, and stable RSA signing key through the approved operator secret channel.
6. Publish the ACM DNS validation records at the external DNS provider and wait for `ISSUED`.
7. Plan `enable_matrix_mas_public_edge=true` to attach the issued certificate, auth-host rule, and desired-zero ECS service. Reject unrelated changes.

## Phase 2: migration rehearsal

Set `enable_matrix_mas_migration_task=true` only with the reviewed account-local digest-pinned wrapper image and populated secret versions. Terraform registers a dedicated Fargate task whose default command is `syn2mas check`; it mounts the existing Synapse EFS access point read-only at `/synapse-data`, uses encrypted NFS transport, and receives secrets only through ECS secret injection. Never copy secret values into shell arguments.

For every phase, save `terraform show -json` output locally and run `scripts/check_matrix_mas_plan.py --phase <infrastructure|public-edge|cutover> PLAN.json`. Do not apply a rejected plan.

Run and inspect:

```text
mas-cli --config /run/mas/config.yaml syn2mas --synapse-config /synapse-data/homeserver.yaml check
mas-cli --config /run/mas/config.yaml syn2mas --synapse-config /synapse-data/homeserver.yaml migrate --dry-run
```

The dry run may execute while Synapse remains online and rolls back its MAS database writes. Record its duration for the maintenance window. Resolve all errors and review every warning. Confirm imported password compatibility uses bcrypt version 1 with `unicode_normalization: true`, followed by argon2id version 2.

Start MAS before cutover only after the dry run, backup coverage, secret checks, incident routing, and account-local digest are verified. Require the private health listener and public `https://auth.zenith-research.ca/.well-known/openid-configuration` to succeed. Existing Matrix login must still reach Synapse while `matrix_mas_cutover_complete=false`.

## Phase 3: maintenance migration and cutover

1. Confirm fresh completed Synapse RDS and EFS backups and a MAS RDS backup.
2. Open the maintenance window.
3. Stop Synapse so no new sessions, tokens, devices, or password changes can race the import.
4. Run the real import:

```text
mas-cli --config /run/mas/config.yaml syn2mas --synapse-config /synapse-data/homeserver.yaml migrate
```

5. If import fails, leave MAS stopped, restore the known-good Synapse configuration, and restart legacy Synapse authentication. The migration tool does not write to the Synapse database.
6. If import succeeds, start MAS and verify its private health, discovery, signing keys, and database connectivity.
7. Plan `matrix_mas_cutover_complete=true`. Require only the expected Synapse task revision and MAS auth/compatibility listener rule changes.
8. Apply and start Synapse.

## Acceptance gate

Require all of the following before closing maintenance:

- Existing user and existing password login succeed.
- An imported pre-cutover access token still reaches account `whoami`.
- Existing refresh, logout, and logout-all semantics succeed.
- Existing devices retain their device IDs and E2EE device keys.
- An encrypted room remains readable and writable from an existing client.
- OAuth authorization-code plus PKCE `S256` succeeds.
- OAuth device authorization succeeds.
- The following endpoint returns HTTP 200 with an HTTPS issuer at `auth.zenith-research.ca` and non-empty authorization, token, registration, revocation, and device-authorization metadata:

```text
https://synapse.zenith-research.ca/_matrix/client/v1/auth_metadata
```

- `/_matrix/client/versions` advertises `"org.matrix.msc4108":true` while Synapse exposes the unstable feature flag.
- Normal non-auth Matrix paths still reach Synapse.
- Public requests to MAS health, metrics, and admin APIs do not succeed.
- Hypha desktop can display the one-time setup QR, the iPhone/iPad can scan it, both devices confirm the short code, E2EE secrets synchronize, and the new device is verified.

## Rollback boundary

Rollback is safe before MAS starts issuing or mutating authentication state: remove the cutover flag, keep compatibility routes on Synapse, and restart the known-good Synapse task. After MAS issues tokens or changes passwords, rollback requires coordinated restoration of Synapse and MAS state and necessarily discards post-cutover authentication changes. Do not improvise a partial rollback.
