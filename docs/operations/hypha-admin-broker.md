# Hypha administration broker operations

The Hypha administration broker is a typed, same-origin façade in front of the stock Synapse Admin API. It accepts a dedicated high-entropy operator secret, stores only its scrypt verifier server-side, and issues short-lived process-local sessions. Its hidden Synapse service administrator and upstream access token never leave the server.

This document describes the reviewed deployment path. It does not claim that the broker image has been published or deployed.

## Security boundary

- Caddy exposes only `/_hypha/admin/v1/**` to the broker, enforcing the broker's 64 KiB request limit before proxying. Matrix client and federation traffic continues to Synapse.
- The broker container is non-root, read-only, capability-free, internal-network-only, and has no database, broad Matrix-data, Docker-socket, or host-port access. Its only writable bind is the dedicated verifier directory described below.
- The runtime secret contains exactly the four existing Synapse values, the operator-secret scrypt verifier, and the broker service password.
- On first broker start, that verifier seeds a mode-0600 verifier file in a dedicated mode-0700 directory on the encrypted persistent Matrix data volume. The raw secret is never written there.
- The raw operator secret is accepted only through protected terminal input or the authenticated same-origin rotation endpoint. It is never accepted on argv, printed, written to Terraform, stored by Hypha, or sent to Synapse.
- Broker sessions are memory-only. Restarting the process or rotating the operator secret revokes every session.

The hidden authority is exactly `@_hypha_admin_broker:<matrix-server-name>`. Bootstrap verifies its identity, administrator role, active/non-guest state, and password login on every rollout. An existing account with a mismatched password or role fails closed.

## First deployment to the existing host

Prerequisites:

1. The backend exact head has passed review and hosted CI.
2. The root-authorized `hypha-synapse-bootstrap` stack is current. It owns the
   retained, immutable `hypha-admin-broker` ECR repository and a dedicated
   GitHub OIDC publisher role trusted only by
   `repo:ZenithResearch/hub:environment:production`.
3. The image workflow has published the reviewed broker source and emitted an
   ECR `@sha256:<64 hex>` reference in account `610992396917`. A custom
   `hypha-admin-broker-<12 hex>` tag is accepted only when that revision is in
   the publishing lineage and every Docker input is byte-identical to the
   workflow revision; the image records both revisions as OCI metadata.
   Publication runs only from the repository's `main` ref, even if the GitHub
   production environment has no independently configured branch policy.
4. The operator has the bounded `zenith-hypha-synapse` profile and the explicit production EC2 instance ID.
5. `verify_fresh_synapse_backup.py` reports `backup_and_restore_verified` for
   the explicit instance, and ordinary Matrix health checks are green. See
   `fresh-synapse-backup-restore.md`; snapshot presence alone is insufficient.

Add the broker fields to the existing runtime secret. The command prompts twice without echo, preserves all existing Synapse values, and prints metadata only:

```bash
python3 scripts/rotate_hypha_admin_broker_secret.py \
  --profile zenith-hypha-synapse \
  --region us-east-1
```

Review the SSM command-bundle hash without changing AWS:

```bash
python3 scripts/deploy_hypha_admin_broker.py \
  --profile zenith-hypha-synapse \
  --region us-east-1 \
  --instance-id i-REVIEWED \
  --hostname synapse.zenith-research.ca \
  --admin-broker-image 610992396917.dkr.ecr.us-east-1.amazonaws.com/hypha-admin-broker@sha256:REVIEWED_DIGEST \
  --dry-run
```

Remove `--dry-run` only after matching the exact image and instance. Before it
sends `AWS-RunShellScript`, the deployer requires fresh application-consistent
root/data snapshots and recent isolated-restore evidence. The rollout command
contains configuration and image identifiers but no secret values. On-host
logic authenticates to the exact private ECR registry through the instance
role and an ephemeral Docker configuration, fetches `AWSCURRENT`, writes
mode-0600 runtime files, verifies the digest,
bootstraps or verifies the service authority, validates Compose, starts only
the broker and Caddy, and checks broker liveness, hidden-service authority
readiness, and ordinary Matrix health from both the container and public route
before committing the rollout.

## Rotation

When `secret_rotation` appears in the authenticated `GET /_hypha/admin/v1/capabilities` response, Hypha can rotate the operator secret from Homeserver Administration. The operator enters and confirms the replacement before submission, so a lost response cannot make the new value unknowable. `POST /_hypha/admin/v1/secret/rotate` then:

1. validates the replacement without reflecting it in errors;
2. derives a salted scrypt verifier;
3. atomically replaces only the mode-0600 verifier file on the encrypted persistent data volume; and
4. swaps the in-process verifier and revokes every existing administration session.

A successful response is empty and never returns either secret or verifier. Hypha clears its local broker session and requires authentication with the replacement. If persistence fails, the runtime verifier and existing sessions remain unchanged.

`rotate_hypha_admin_broker_secret.py` remains the emergency bootstrap/recovery path. It updates the Secrets Manager seed but deliberately does not override an existing durable verifier file. To recover from a lost operator secret, stop the broker, move the verifier file into a protected backup location, rotate the bootstrap seed through the terminal utility, and redeploy; the next start seeds a new verifier file. Do not delete the existing verifier while the broker is running.

Service-password rotation is not an ordinary operator-secret rotation. It requires a separately reviewed sequence that changes the existing Synapse service account password and the runtime secret as one controlled operation.

## Verification

After deployment, verify without placing secrets in shell history or screenshots:

```bash
curl --fail --silent --show-error \
  https://synapse.zenith-research.ca/_hypha/admin/v1/health

curl --fail --silent --show-error \
  https://synapse.zenith-research.ca/_hypha/admin/v1/ready

curl --fail --silent --show-error \
  https://synapse.zenith-research.ca/_matrix/client/versions >/dev/null
```

Then use protected input in Hypha to check a session exchange, read-only snapshot, and logout. A wrong secret must fail generically. Restarting `hypha-admin-broker` must invalidate the prior session without changing the ordinary Matrix account, devices, rooms, E2EE state, or Synapse client/federation health.

Do not capture the secret request body, bearer token, service account password, upstream access token, `.env` files, Secrets Manager value, or full SSM output as evidence.

## Rollback

Before mutation, the deployer creates a timestamped backup under `/opt/matrix/backups/`. Any rollout error through the on-host public readiness and Matrix checks stops the broker, restores the prior `compose.yaml`, `Caddyfile`, and broker environment state, and reconciles the prior Compose stack with `--remove-orphans`. The hidden service account may remain after a first bootstrap, but it is not publicly discoverable through broker snapshots and has no route or stored token outside the server. PostgreSQL, Synapse configuration/data, the EBS volume, and Terraform-managed EC2 resources are not replaced or modified.

If post-deployment monitoring finds a problem, restore the exact backup through a reviewed SSM command, stop/remove only `hypha-admin-broker`, and run the restored Compose stack. Reverify Matrix client/federation health before declaring rollback complete.
