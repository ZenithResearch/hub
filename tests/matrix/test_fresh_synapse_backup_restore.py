import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def load_script(name: str):
    path = ROOT / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tags(**values):
    return [{"Key": key, "Value": value} for key, value in values.items()]


def snapshot(snapshot_id, volume_id, backup_class, started, **extra_tags):
    return {
        "SnapshotId": snapshot_id,
        "VolumeId": volume_id,
        "State": "completed",
        "StartTime": started,
        "Tags": tags(
            HyphaBackupClass=backup_class,
            **{
                "aws:dlm:pre-script": "SUCCESS",
                "aws:dlm:post-script": "SUCCESS",
                **extra_tags,
            },
        ),
    }


def aws_responses():
    instance = {
        "InstanceId": "i-0123456789abcdef0",
        "State": {"Name": "running"},
        "Placement": {"AvailabilityZone": "us-east-1a"},
        "RootDeviceName": "/dev/xvda",
        "BlockDeviceMappings": [
            {"DeviceName": "/dev/xvda", "Ebs": {"VolumeId": "vol-00000000000000001"}},
            {"DeviceName": "/dev/sdf", "Ebs": {"VolumeId": "vol-00000000000000002"}},
        ],
        "Tags": tags(Name="hypha-fresh-synapse", Project="hypha", Component="fresh-synapse"),
    }
    volumes = [
        {
            "VolumeId": "vol-00000000000000001",
            "Encrypted": True,
            "Tags": tags(Project="hypha", Component="fresh-synapse"),
        },
        {
            "VolumeId": "vol-00000000000000002",
            "Encrypted": True,
            "Tags": tags(
                Name="hypha-fresh-synapse-data",
                Project="hypha",
                Component="fresh-synapse",
            ),
        },
    ]
    script = {
        "ExecutionHandler": "HyphaSynapseApplicationConsistentSnapshot",
        "ExecutionHandlerService": "AWS_SYSTEMS_MANAGER",
        "ExecuteOperationOnScriptFailure": False,
        "ExecutionTimeout": 120,
        "MaximumRetryCount": 3,
        "Stages": ["PRE", "POST"],
    }
    schedules = []
    for name, backup_class, interval, retention in (
        ("Hourly application-consistent snapshots", "hourly", 1, 72),
        ("Daily restore-rehearsal snapshots", "daily", 24, 35),
    ):
        schedules.append(
            {
                "Name": name,
                "CopyTags": True,
                "Tags": tags(HyphaBackupClass=backup_class),
                "CreateRule": {
                    "Interval": interval,
                    "IntervalUnit": "HOURS",
                    "Scripts": [dict(script)],
                },
                "RetainRule": {"Count": retention},
            }
        )
    snapshots = [
        snapshot(
            "snap-00000000000000001",
            "vol-00000000000000001",
            "hourly",
            "2026-08-23T11:00:00Z",
        ),
        snapshot(
            "snap-00000000000000002",
            "vol-00000000000000002",
            "hourly",
            "2026-08-23T11:00:00Z",
        ),
        snapshot(
            "snap-00000000000000003",
            "vol-00000000000000001",
            "daily",
            "2026-08-23T08:00:00Z",
        ),
        snapshot(
            "snap-00000000000000004",
            "vol-00000000000000002",
            "daily",
            "2026-08-23T08:00:00Z",
            HyphaRestoreVerifiedAt="2026-08-22T12:00:00Z",
            HyphaRestoreVerifierVersion="1",
        ),
    ]
    return {
        "describe-instances": {"Reservations": [{"Instances": [instance]}]},
        "describe-volumes": {"Volumes": volumes},
        "get-lifecycle-policies": {
            "Policies": [
                {
                    "Description": "Hypha fresh Synapse application-consistent EBS snapshots",
                    "PolicyId": "policy-0123456789abcdef0",
                    "State": "ENABLED",
                }
            ]
        },
        "get-lifecycle-policy": {
            "Policy": {
                "State": "ENABLED",
                "ExecutionRoleArn": "arn:aws:iam::610992396917:role/HyphaSynapseDlmRole",
                "PolicyDetails": {
                    "PolicyType": "EBS_SNAPSHOT_MANAGEMENT",
                    "ResourceTypes": ["INSTANCE"],
                    "TargetTags": [
                        {"Key": "Name", "Value": "hypha-fresh-synapse"},
                        {"Key": "Project", "Value": "hypha"},
                        {"Key": "Component", "Value": "fresh-synapse"},
                    ],
                    "Parameters": {"ExcludeBootVolume": False},
                    "Schedules": schedules,
                },
            }
        },
        "describe-snapshots": {"Snapshots": snapshots},
    }


def runner(responses):
    def run(arguments):
        operation = arguments[1]
        assert operation in responses
        return json.dumps(responses[operation])

    return run


def test_cloudformation_creates_fail_closed_application_consistent_backups():
    template = read("infra/matrix/aws/bootstrap.yaml")

    for marker in [
        "AWS::SSM::Document",
        "HyphaSynapseApplicationConsistentSnapshot",
        "DLMScriptsAccess",
        "default: dry-run",
        "default: None",
        "platformType",
        "CHECKPOINT;",
        "/usr/sbin/xfs_freeze -f",
        "/usr/sbin/xfs_freeze -u",
        "hypha-dlm-auto-thaw",
        "AWS::DLM::LifecyclePolicy",
        "ResourceTypes:",
        "- INSTANCE",
        "Hourly application-consistent snapshots",
        "Daily restore-rehearsal snapshots",
        "HyphaBackupClass",
        "ExecuteOperationOnScriptFailure: false",
        "ExecutionTimeout: 120",
        "MaximumRetryCount: 3",
        "Count: 72",
        "Count: 35",
    ]:
        assert marker in template
    assert template.count("ExecuteOperationOnScriptFailure: false") == 2
    assert template.count("DeletionPolicy: Retain") >= 4
    assert "--password" not in template


def test_cloudformation_backup_template_parses_and_iam_policies_fit_limits():
    class CloudFormationLoader(yaml.SafeLoader):
        pass

    def construct_intrinsic(loader, _suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    CloudFormationLoader.add_multi_constructor("!", construct_intrinsic)
    template = yaml.load(
        read("infra/matrix/aws/bootstrap.yaml"),
        Loader=CloudFormationLoader,
    )
    resources = template["Resources"]
    assert (
        resources["HyphaSynapseSnapshotDocument"]["Properties"]["Content"]["schemaVersion"] == "2.2"
    )
    assert resources["HyphaSynapseBackupPolicy"]["Properties"]["State"] == "ENABLED"
    for role_name in ("HyphaSynapseDlmRole", "HyphaSynapseDeploymentRole"):
        policies = resources[role_name]["Properties"]["Policies"]
        for policy in policies:
            encoded = json.dumps(policy["PolicyDocument"], separators=(",", ":"))
            assert len(encoded) < 10_240

    deployment_policies = {
        policy["PolicyName"]: policy["PolicyDocument"]
        for policy in resources["HyphaSynapseDeploymentRole"]["Properties"]["Policies"]
    }
    rehearsal_statements = {
        statement["Sid"]: statement
        for statement in deployment_policies["HyphaSynapseRestoreRehearsal"]["Statement"]
    }
    evidence = rehearsal_statements["RecordExactSynapseRestoreEvidence"]
    assert evidence["Action"] == "ec2:CreateTags"
    assert evidence["Resource"] == "arn:aws:ec2:us-east-1::snapshot/*"
    evidence_conditions = evidence["Condition"]
    assert evidence_conditions["StringEquals"] == {
        "ec2:ResourceTag/aws:dlm:lifecycle-policy-id": "HyphaSynapseBackupPolicy",
        "ec2:ResourceTag/aws:dlm/pre-script": "SUCCESS",
        "ec2:ResourceTag/aws:dlm/post-script": "SUCCESS",
        "ec2:ResourceTag/HyphaBackupClass": "daily",
        "ec2:ResourceTag/Project": "hypha",
        "ec2:ResourceTag/Component": "fresh-synapse",
        "aws:RequestTag/HyphaRestoreVerifierVersion": "1",
        "aws:RequestedRegion": "us-east-1",
    }
    assert evidence_conditions["ForAllValues:StringEquals"]["aws:TagKeys"] == [
        "HyphaRestoreVerifiedAt",
        "HyphaRestoreVerifierVersion",
    ]
    assert evidence_conditions["Null"]["aws:RequestTag/HyphaRestoreVerifiedAt"] == "false"

    for sid in ("DetachRestoreRehearsalVolume", "DeleteRestoreRehearsalVolume"):
        statement = rehearsal_statements[sid]
        conditions = statement["Condition"]["StringEquals"]
        assert statement["Resource"] == "arn:aws:ec2:us-east-1:610992396917:volume/*"
        assert conditions["ec2:ResourceTag/Purpose"] == "restore-rehearsal"
        assert conditions["ec2:ResourceTag/ManagedBy"] == "restore-rehearsal"
        assert conditions["ec2:ResourceTag/Project"] == "hypha"
        assert conditions["ec2:ResourceTag/Component"] == "fresh-synapse"

    instance_detach = rehearsal_statements["DetachRestoreOnlyFromExactSynapseInstance"]
    assert instance_detach["Action"] == "ec2:DetachVolume"
    assert instance_detach["Resource"] == "arn:aws:ec2:us-east-1:610992396917:instance/*"
    assert instance_detach["Condition"]["StringEquals"]["ec2:ResourceTag/Name"] == (
        "hypha-fresh-synapse"
    )


def test_verifier_accepts_only_fresh_app_consistent_snapshots_and_restore_evidence():
    verifier = load_script("verify_fresh_synapse_backup")
    result = verifier.verify_backup(
        "i-0123456789abcdef0",
        runner(responses := aws_responses()),
        now=NOW,
    )

    assert result["status"] == "backup_and_restore_verified"
    assert result["data_daily_snapshot_id"] == "snap-00000000000000004"
    assert result["restore_verified_snapshot_id"] == "snap-00000000000000004"
    assert "Snapshots" in responses["describe-snapshots"]


def test_verifier_rejects_crash_consistent_or_stale_snapshot():
    verifier = load_script("verify_fresh_synapse_backup")
    responses = aws_responses()
    data_hourly = responses["describe-snapshots"]["Snapshots"][1]
    data_hourly["Tags"][2]["Value"] = "FAILED"

    with pytest.raises(verifier.BackupVerificationError, match="snapshot is missing"):
        verifier.verify_backup("i-0123456789abcdef0", runner(responses), now=NOW)

    responses = aws_responses()
    responses["describe-snapshots"]["Snapshots"][1]["StartTime"] = "2026-08-23T06:00:00Z"
    with pytest.raises(verifier.BackupVerificationError, match="snapshot is stale"):
        verifier.verify_backup("i-0123456789abcdef0", runner(responses), now=NOW)


def test_verifier_rejects_missing_restore_evidence_and_crash_fallback():
    verifier = load_script("verify_fresh_synapse_backup")
    responses = aws_responses()
    responses["describe-snapshots"]["Snapshots"][3]["Tags"] = responses["describe-snapshots"][
        "Snapshots"
    ][3]["Tags"][:3]
    with pytest.raises(verifier.BackupVerificationError, match="restore evidence is missing"):
        verifier.verify_backup("i-0123456789abcdef0", runner(responses), now=NOW)

    responses = aws_responses()
    schedules = responses["get-lifecycle-policy"]["Policy"]["PolicyDetails"]["Schedules"]
    schedules[0]["CreateRule"]["Scripts"][0]["ExecuteOperationOnScriptFailure"] = True
    with pytest.raises(verifier.BackupVerificationError, match="schedule contract is invalid"):
        verifier.verify_backup("i-0123456789abcdef0", runner(responses), now=NOW)


def test_restore_rehearsal_is_isolated_and_cleanup_precedes_evidence():
    script = read("scripts/rehearse_fresh_synapse_restore.py")
    deployer = read("scripts/deploy_hypha_admin_broker.py")

    for marker in [
        "--network none",
        "--read-only",
        "nouuid,nodev,nosuid,noexec",
        "server.signing.key",
        "media_store",
        "information_schema.tables",
        "detach-volume",
        "delete-volume",
        "volume-deleted",
        "HyphaRestoreVerifiedAt",
        "HyphaRestoreVerifierVersion",
    ]:
        assert marker in script
    rehearsal = script[script.index("def rehearse_restore(") :]
    assert rehearsal.index("_delete_restore_volume") < rehearsal.index("_record_restore_evidence")
    main = deployer[deployer.index("def main(") :]
    assert main.index("backup.verify_backup") < main.index("_send_command")


def test_restore_failure_cleans_up_without_recording_evidence(monkeypatch):
    sys_path = list(__import__("sys").path)
    __import__("sys").path.insert(0, str(ROOT / "scripts"))
    try:
        restore = load_script("rehearse_fresh_synapse_restore")
    finally:
        __import__("sys").path[:] = sys_path
    calls = []
    monkeypatch.setattr(
        restore.backup,
        "verify_backup",
        lambda *args, **kwargs: {
            "availability_zone": "us-east-1a",
            "data_daily_snapshot_id": "snap-00000000000000004",
        },
    )
    monkeypatch.setattr(restore, "_create_restore_volume", lambda *args: "vol-00000000000000009")
    monkeypatch.setattr(restore, "_attach_restore_volume", lambda *args: calls.append("attach"))
    monkeypatch.setattr(restore, "_send_command", lambda *args: "command-id")
    monkeypatch.setattr(
        restore,
        "_wait_for_command",
        lambda *args: (_ for _ in ()).throw(restore.RestoreRehearsalError("failed")),
    )
    monkeypatch.setattr(
        restore,
        "_wait_for_terminal_cleanup",
        lambda *args: calls.append("terminal"),
    )
    monkeypatch.setattr(
        restore, "_delete_restore_volume", lambda *args, **kwargs: calls.append("delete")
    )
    monkeypatch.setattr(restore, "_record_restore_evidence", lambda *args: calls.append("record"))

    with pytest.raises(restore.RestoreRehearsalError, match="isolated restore verification failed"):
        restore.rehearse_restore("i-0123456789abcdef0")
    assert calls == ["attach", "terminal", "delete"]


def test_ambiguous_command_dispatch_leaves_volume_attached_without_evidence(monkeypatch):
    sys_path = list(__import__("sys").path)
    __import__("sys").path.insert(0, str(ROOT / "scripts"))
    try:
        restore = load_script("rehearse_fresh_synapse_restore")
    finally:
        __import__("sys").path[:] = sys_path
    calls = []
    monkeypatch.setattr(
        restore.backup,
        "verify_backup",
        lambda *args, **kwargs: {
            "availability_zone": "us-east-1a",
            "data_daily_snapshot_id": "snap-00000000000000004",
        },
    )
    monkeypatch.setattr(restore, "_create_restore_volume", lambda *args: "vol-00000000000000009")
    monkeypatch.setattr(restore, "_attach_restore_volume", lambda *args: calls.append("attach"))
    monkeypatch.setattr(
        restore,
        "_send_command",
        lambda *args: (_ for _ in ()).throw(restore.RestoreRehearsalError("ambiguous")),
    )
    monkeypatch.setattr(
        restore, "_delete_restore_volume", lambda *args, **kwargs: calls.append("delete")
    )
    monkeypatch.setattr(restore, "_record_restore_evidence", lambda *args: calls.append("record"))

    with pytest.raises(restore.RestoreRehearsalError, match="cleanup failed"):
        restore.rehearse_restore("i-0123456789abcdef0")
    assert calls == ["attach"]
