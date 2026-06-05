# Matrix backup and restore minimum

ISS-P14-005 defines the minimum backup/restore contract before production Synapse can be called durable.

## State classes that must be restorable

- Postgres database
- media store
- homeserver signing key
- homeserver config
- appservice tokens

## Backup contract

- Backup vault: `aws_backup_vault.matrix`
- Backup plan: `aws_backup_plan.matrix`
- Resource selection: `matrix_backup_resource_arns`
- Schedule: `matrix_backup_schedule`
- Retention: `matrix_backup_retention_days`
- Restore owner: Hub operator
- Escalation/contact: the Hub production operator who owns the accepted production Terraform plan/apply for PRP-PR-014

An empty `matrix_backup_resource_arns` list means the Terraform contract can create the vault/plan without selecting protected resources. That state is contract-only and is not a production backup posture.

## Operator evidence commands

```bash
aws backup list-backup-vaults
aws backup list-recovery-points-by-backup-vault --backup-vault-name <vault>
aws backup get-backup-plan --backup-plan-id <plan-id>
```

## Restore workflow

Untested restore paths are unproven. This workflow is the explicit operator path to rehearse in non-production before durable production claims.

1. Confirm the restore owner is present and operating under the approved Hub production role/session for the environment being restored.
2. Use `aws backup list-recovery-points-by-backup-vault --backup-vault-name <vault>` to select a recovery point for the concrete Matrix state resource.
3. Verify the restore job role and IAM permissions before starting: missing IAM permissions are a hard stop, not a best-effort restore.
4. restore to a non-production target first. Do not overwrite production state until the recovery point, account, region, and AZ are confirmed.
5. restore order:
   1. durable database state / Postgres snapshot or volume;
   2. media store objects or volume;
   3. homeserver signing key;
   4. homeserver config, including server name and database/media pointers;
   5. appservice registration and appservice token material from the approved secret backend.
6. Reconnect the restored Synapse target to the Matrix target group only after config and secret classes are present.
7. validate `/_matrix/client/versions` on the restored target before calling the restore usable.
8. Run federation/client smoke checks only after the target is reachable and the ALB/DNS path is intentionally pointed at the restored target.

## Restore failure and decision boundaries

- missing IAM permissions: stop and fix the operator role/policy before restore.
- wrong account or AZ: stop before attaching restored state to a live Synapse target.
- partial restore: treat database/media/signing-key/config/token classes independently and do not claim success until every required class is accounted for.
- token rotation during restore: pause and reconcile appservice `as_token`/`hs_token`, registration shared secret, and macaroon secret versions before starting Synapse.
- config drift between backup time and runtime: document the drift and choose restore-time config intentionally.
- empty `matrix_backup_resource_arns`: no protected resources were selected, so there is nothing to restore from the plan.

## Restore boundary

Untested restore paths are unproven. A successful backup plan or snapshot listing is not proof that Synapse can be restored.

Do not claim durable production Synapse until a non-prod restore dry run or explicitly accepted equivalent evidence verifies database, media, signing-key, config, and appservice-token recovery.
