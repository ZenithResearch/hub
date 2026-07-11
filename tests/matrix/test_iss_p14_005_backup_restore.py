from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def read(rel: str) -> str:
    return (ROOT / rel).read_text()

def test_matrix_backup_plan_declares_vault_schedule_retention_and_selection():
    variables = read("infra/aws_baseline_80/variables.tf")
    backup = read("infra/aws_baseline_80/matrix_backup.tf")
    assert 'variable "enable_matrix_backup"' in variables
    assert 'variable "matrix_backup_retention_days"' in variables
    assert 'variable "matrix_backup_resource_arns"' in variables
    assert 'resource "aws_backup_vault" "matrix"' in backup
    assert 'resource "aws_backup_plan" "matrix"' in backup
    assert 'schedule          = var.matrix_backup_schedule' in backup
    assert 'delete_after = var.matrix_backup_retention_days' in backup
    assert 'resource "aws_backup_selection" "matrix"' in backup
    assert 'resources = concat(' in backup
    assert 'var.matrix_backup_resource_arns' in backup
    assert 'aws_db_instance.matrix_synapse[0].arn' in backup
    assert 'aws_efs_file_system.matrix_synapse[0].arn' in backup
    assert 'AWSBackupServiceRolePolicyForRestores' in backup

def test_matrix_restore_runbook_names_state_classes_and_unproven_restore_boundary():
    doc = read("docs/operations/matrix-backup-restore.md")
    for phrase in ['Postgres database', 'media store', 'homeserver signing key', 'homeserver config', 'appservice tokens']:
        assert phrase in doc
    assert 'Untested restore paths are unproven' in doc
    assert 'Do not claim durable production Synapse' in doc
    assert 'aws backup list-recovery-points-by-backup-vault' in doc
    assert 'Restore owner: Hub operator' in doc
    assert 'Restore workflow' in doc
    assert 'RPO' in doc and '24 hours' in doc
    assert 'RTO' in doc and '120 minutes' in doc
    assert 'start-to-usable' in doc
    for phrase in [
        'select a recovery point',
        'restore to a non-production target',
        'restore job role',
        'restore order',
        'validate `/_matrix/client/versions`',
    ]:
        assert phrase in doc


def test_matrix_backup_runbook_records_empty_selection_and_restore_failure_boundaries():
    doc = read("docs/operations/matrix-backup-restore.md")
    for phrase in [
        'empty `matrix_backup_resource_arns`',
        'missing IAM permissions',
        'wrong account or AZ',
        'partial restore',
        'token rotation during restore',
    ]:
        assert phrase in doc

def test_matrix_backup_contract_does_not_store_raw_secrets():
    backup = read("infra/aws_baseline_80/matrix_backup.tf")
    for forbidden in ['as_token', 'hs_token', 'macaroon_secret', 'registration_shared_secret']:
        assert forbidden not in backup
