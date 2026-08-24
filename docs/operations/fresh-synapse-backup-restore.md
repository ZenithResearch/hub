# Fresh Synapse EC2 backup and restore gate

This runbook is the production recovery contract for the standalone fresh
Synapse EC2 stack under `infra/matrix/aws`. It is separate from the RDS/EFS
Matrix recovery path in `matrix-backup-restore.md`.

## Enforced recovery posture

The root-authorized bootstrap stack owns one enabled Amazon Data Lifecycle
Manager policy targeting only the instance tagged `Name=hypha-fresh-synapse`.
Every run creates one multi-volume snapshot set containing both the root volume
and the dedicated `hypha-fresh-synapse-data` volume.

Before snapshot initiation, the custom SSM document verifies the exact XFS
mount and running `matrix-db`, executes a PostgreSQL `CHECKPOINT`, schedules a
110-second automatic thaw, and freezes `/opt/matrix-data`. DLM initiates the
multi-volume snapshot and then invokes the post-script to thaw the filesystem.
The policy sets `ExecuteOperationOnScriptFailure: false`: a pre-script failure
skips the snapshot rather than silently producing a crash-consistent backup.

The two schedules are:

- hourly application-consistent sets, with the latest 72 retained;
- daily restore-rehearsal sets at 08:00 UTC, with the latest 35 retained.

The deployment gate requires completed root and data snapshots carrying both
`aws:dlm:pre-script=SUCCESS` and `aws:dlm:post-script=SUCCESS`. Hourly sets may
be at most three hours old and daily sets at most 26 hours old. A recent
successful isolated restore must be recorded on a retained daily data snapshot
within the last 30 days. Creating a policy, seeing a snapshot ID, or mounting a
filesystem is not restore evidence.

## Bootstrap or repair the policy

The CloudFormation bootstrap requires the exact target-account root profile.
Reauthenticate that profile through the configured AWS login flow, review the
template diff, and run:

```bash
python3 scripts/bootstrap_fresh_synapse_account.py \
  --profile zenith-hypha-free \
  --region us-east-1
```

An existing stack update does not ask for or change the alert email. Wait for
both schedules to emit completed, script-successful root and data snapshots.
The first daily snapshot can take up to 24 hours after policy creation.

Verify the current gate without reading workload data or secret values:

```bash
python3 scripts/verify_fresh_synapse_backup.py \
  --profile zenith-hypha-synapse \
  --region us-east-1 \
  --instance-id i-REVIEWED
```

The command prints resource IDs and status only. It fails until the isolated
restore rehearsal below has succeeded.

## Isolated restore rehearsal

First inspect the exact daily snapshot that would be exercised:

```bash
python3 scripts/rehearse_fresh_synapse_restore.py \
  --profile zenith-hypha-synapse \
  --region us-east-1 \
  --instance-id i-REVIEWED \
  --dry-run
```

Remove `--dry-run` only after confirming the production instance and snapshot.
The rehearsal:

1. creates one encrypted, tagged temporary EBS volume in the production
   instance's availability zone from the latest daily data snapshot;
2. attaches it at a non-production device name and resolves it by immutable
   Nitro EBS volume ID;
3. mounts the restored XFS copy with `nouuid,nodev,nosuid,noexec`;
4. starts the exact production PostgreSQL image with no network, no ports, a
   read-only container root, and only the restored database directory writable;
5. requires PostgreSQL readiness, core Synapse tables, the signing key, and the
   media-store directory without printing database rows, keys, media, or secrets;
6. stops PostgreSQL, unmounts, detaches, deletes, and waits for deletion of the
   temporary volume; and
7. only after cleanup, tags the source DLM snapshot with the verifier version
   and UTC completion time, then reruns the strict gate.

Any validation, SSM, database, unmount, detach, delete, or evidence-write error
fails the rehearsal. A failed rehearsal never writes success evidence. Inspect
the tagged temporary volume and SSM command status before retrying. If AWS does
not return a command ID after dispatch is attempted, the rehearsal deliberately
leaves the tagged volume attached because it cannot prove the host command has
stopped. Do not use force-detach or delete an unverified volume.

## Broker deployment gate

`deploy_hypha_admin_broker.py` executes the strict backup/restore verifier after
establishing the bounded deployment identity and before sending any rollout
command to the host. There is no command-line bypass. A stale snapshot, failed
pre/post script, unexpected attached volume, policy drift, or expired restore
evidence prevents the broker rollout from starting.

## Recovery boundary

The inline production EBS volumes still use delete-on-termination. Deleting the
Terraform runtime therefore deletes those live volumes, but the retained DLM
snapshots remain governed by their schedules and can seed a reviewed recovery.
Restoring production service from them is a separate incident operation: create
replacement infrastructure, recover the root/config and data classes together,
reconcile runtime Secrets Manager values, and verify Matrix client/federation
health before changing DNS. This rehearsal never mutates or replaces the live
PostgreSQL, Synapse, root volume, or data volume.
