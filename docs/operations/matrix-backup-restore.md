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

## Operator evidence commands

```bash
aws backup list-backup-vaults
aws backup list-recovery-points-by-backup-vault --backup-vault-name <vault>
aws backup get-backup-plan --backup-plan-id <plan-id>
```

## Restore boundary

Untested restore paths are unproven. A successful backup plan or snapshot listing is not proof that Synapse can be restored.

Do not claim durable production Synapse until a non-prod restore dry run or explicitly accepted equivalent evidence verifies database, media, signing-key, config, and appservice-token recovery.
