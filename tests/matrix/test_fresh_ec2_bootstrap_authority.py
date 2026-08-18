import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def load_bootstrap():
    path = ROOT / "scripts/bootstrap_fresh_synapse_account.py"
    spec = importlib.util.spec_from_file_location("bootstrap_fresh_synapse_account", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_template_creates_private_durable_state_and_non_root_role():
    template = read("infra/matrix/aws/bootstrap.yaml")

    assert "AWS::S3::Bucket" in template
    assert "BucketEncryption:" in template
    assert "VersioningConfiguration:" in template
    assert "Status: Enabled" in template
    assert "PublicAccessBlockConfiguration:" in template
    assert template.count("DeletionPolicy: Retain") >= 1
    assert "AWS::IAM::Role" in template
    assert "AWS::IAM::User" in template
    assert "HyphaSynapseTerraformSource" in template
    assert "HyphaSynapseInstanceBoundary" in template
    assert "iam:PermissionsBoundary" in template
    assert "iam:PassedToService: ec2.amazonaws.com" in template
    assert "iam:PolicyARN: arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" in template
    assert "HyphaSynapseDeploymentRole" in template
    assert "DependsOn: HyphaSynapseTerraformSource" in template
    assert "sts:AssumeRole" in template
    assert "arn:aws:iam::610992396917:user/HyphaSynapseTerraformSource" in template
    assert "arn:aws:iam::610992396917:root" not in template


def test_deployment_policy_is_service_bounded_and_cannot_touch_castalia_bucket():
    template = read("infra/matrix/aws/bootstrap.yaml")
    runtime = read("infra/matrix/aws/main.tf")

    for service in ["ec2:", "iam:", "secretsmanager:", "ssm:", "s3:"]:
        assert service in template
    assert "AdministratorAccess" not in template
    assert "PowerUserAccess" not in template
    assert "castalia-descriptor-store" not in template
    assert "hypha-synapse-terraform-state" in template
    assert "hypha/fresh-synapse/runtime" in template
    assert "iam:PassRole" in template
    assert "ec2:DescribeInstanceCreditSpecifications" in template
    assert "ec2:DescribeAddressesAttribute" in template
    assert "ec2:RebootInstances" in template
    assert "ec2:ResourceTag/Project: hypha" in template
    assert "ec2:ResourceTag/Component: fresh-synapse" in template
    assert "ManageTaggedSynapseSecurityGroupRules" in template
    assert "TagOnlyDuringSynapseResourceCreation" in template
    assert "ec2:CreateAction:" in template
    assert "aws:RequestTag/Project: hypha" in template
    assert "aws:RequestTag/Component: fresh-synapse" in template
    assert "ConfigureTaggedSynapseInfrastructure" in template
    create_start = template.index("- Sid: CreateSynapseInfrastructureInTargetRegion")
    create_end = template.index("- Sid: TagOnlyDuringSynapseResourceCreation")
    create_statement = template[create_start:create_end]
    assert "aws:RequestTag/Project: hypha" in create_statement
    assert "aws:RequestTag/Component: fresh-synapse" in create_statement
    assert "SsmTaggedSynapseInstance" in template
    assert "ssm:resourceTag/Project: hypha" in template
    assert "ssm:resourceTag/Component: fresh-synapse" in template
    assert "arn:aws:ssm:us-east-1::document/AWS-RunShellScript" in template
    assert "arn:aws:ssm:us-east-1:610992396917:document/SSM-SessionManagerRunShell" in template
    assert "arn:aws:ssm:us-east-1:610992396917:session/hypha-synapse-deploy-*" in template
    assert "ssm:GetCommandInvocation" not in template
    assert "ssm:CancelCommand" not in template
    assert "ec2:TerminateInstances" not in template
    assert "ec2:StopInstances" not in template
    assert "ec2:DeleteTags" not in template
    assert "ec2:ModifyInstanceAttribute" not in template
    assert "ec2:ModifyVolume" not in template
    assert "ec2:ReplaceIamInstanceProfileAssociation" not in template
    assert "iam:DeleteRolePermissionsBoundary" not in template
    assert "iam:UpdateAssumeRolePolicy" not in template
    assert "secretsmanager:PutResourcePolicy" not in template
    assert "ssm:ListCommandInvocations" not in template
    secret_start = template.index("- Sid: ProductionMatrixSecrets")
    secret_end = template.index("- Sid: SsmManagedInstanceMetadata")
    deployment_secret_statement = template[secret_start:secret_end]
    assert "secretsmanager:GetSecretValue" not in deployment_secret_statement
    assert "arn:aws:iam::610992396917:role/hypha-fresh-synapse" in template
    assert "arn:aws:iam::610992396917:instance-profile/hypha-fresh-synapse" in template
    assert "ssm:StartSession" in template
    assert "ssm:ResumeSession" in template
    assert "ssm:TerminateSession" in template
    assert 'name = "hypha-fresh-synapse"' in runtime
    assert 'name                    = "hypha/fresh-synapse/runtime"' in runtime


def test_bootstrap_script_fails_closed_on_profile_account_region_and_principal():
    script = read("scripts/bootstrap_fresh_synapse_account.py")

    for marker in [
        'EXPECTED_PROFILE = "zenith-hypha-free"',
        'EXPECTED_ACCOUNT = "610992396917"',
        'EXPECTED_REGION = "us-east-1"',
        'arn:aws:iam::610992396917:root',
        "get-caller-identity",
        "deploy",
        "hypha-synapse-bootstrap",
        "getpass.getpass",
        "AlertEmail=",
        "ParameterKey=AlertEmail,UsePreviousValue=true",
        "if not stack_exists():",
        "NamedTemporaryFile",
        "os.chmod",
        "0o600",
        '"file://" + parameter_path',
        "os.unlink(parameter_path)",
        'EXPECTED_SOURCE_USER = "HyphaSynapseTerraformSource"',
        'SOURCE_PROFILE = "zenith-hypha-bootstrap"',
        'DEPLOYMENT_PROFILE = "zenith-hypha-synapse"',
        '"iam", "create-access-key"',
        '"delete-access-key"',
        "stored_key_id != live_key_id",
        '"sts", "get-caller-identity"',
        'config.set(deployment_section, "role_arn", EXPECTED_ROLE_ARN)',
        'config.set(deployment_section, "role_session_name", DEPLOYMENT_ROLE_SESSION_NAME)',
        'os.chmod(path, 0o600)',
    ]:
        assert marker in script
    assert "SessionToken" not in script
    assert "print(credentials" not in script
    assert "print(alert_email" not in script
    assert "print(stored_secret" not in script


def test_bootstrap_rotates_one_orphaned_remote_key_before_local_install(monkeypatch, tmp_path):
    bootstrap = load_bootstrap()
    calls = []

    def fake_root_aws(arguments):
        calls.append(tuple(arguments))
        if arguments[:2] == ("iam", "list-access-keys"):
            return json.dumps({"AccessKeyMetadata": [{"AccessKeyId": "ORPHANED"}]})
        if arguments[:2] == ("iam", "delete-access-key"):
            return ""
        if arguments[:2] == ("iam", "create-access-key"):
            return json.dumps(
                {"AccessKey": {"AccessKeyId": "REPLACEMENT", "SecretAccessKey": "replacement-secret"}}
            )
        raise AssertionError(arguments)

    def fake_profile_aws(profile, arguments):
        if profile == bootstrap.SOURCE_PROFILE:
            return json.dumps(
                {"Account": bootstrap.EXPECTED_ACCOUNT, "Arn": "arn:aws:iam::610992396917:user/HyphaSynapseTerraformSource"}
            )
        return json.dumps(
            {
                "Account": bootstrap.EXPECTED_ACCOUNT,
                "Arn": "arn:aws:sts::610992396917:assumed-role/HyphaSynapseDeploymentRole/test",
            }
        )

    monkeypatch.setattr(bootstrap.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "_run_aws", fake_root_aws)
    monkeypatch.setattr(bootstrap, "_run_aws_profile", fake_profile_aws)

    bootstrap._configure_local_profiles()

    credentials = (tmp_path / ".aws/credentials").read_text(encoding="utf-8")
    assert "REPLACEMENT" in credentials
    assert "replacement-secret" in credentials
    assert "ORPHANED" not in credentials
    assert any(call[:2] == ("iam", "delete-access-key") for call in calls)
    assert any(call[:2] == ("iam", "create-access-key") for call in calls)


def test_bootstrap_creates_budget_and_dated_expiry_alerts_without_storing_email_in_source():
    template = read("infra/matrix/aws/bootstrap.yaml")

    assert "AlertEmail:" in template
    assert "NoEcho: true" in template
    assert "AWS::Budgets::Budget" in template
    assert "BudgetName: hypha-synapse-monthly" in template
    assert "Amount: 30" in template
    assert "AWS::SNS::Topic" in template
    assert template.count("AWS::Scheduler::Schedule") == 4
    for timestamp in [
        "2026-12-19T20:08:42",
        "2027-01-18T20:08:42",
        "2027-02-03T20:08:42",
        "2027-02-10T20:08:42",
    ]:
        assert timestamp in template
    assert "AWS::Scheduler::Schedule" in template
    assert "sns:Publish" in template
    assert "s3:GetEncryptionConfiguration" in template
    assert "s3:GetBucketEncryption" not in template
    assert "ActionAfterCompletion" not in template


def test_runtime_backend_uses_isolated_s3_state_and_lockfile():
    main = read("infra/matrix/aws/main.tf")
    backend = read("infra/matrix/aws/backend.tf")
    example = read("infra/matrix/aws/backend.hcl.example")

    assert 'required_version = "= 1.14.0"' in main
    assert 'version = "= 5.100.0"' in main
    assert 'version     = "5.100.0"' in read("infra/matrix/aws/.terraform.lock.hcl")
    assert 'backend "s3"' in backend
    assert "use_lockfile = true" in example
    assert 'profile      = "zenith-hypha-bootstrap"' in example
    assert 'profile      = "zenith-hypha-free"' not in example
    assert 'key          = "fresh-synapse/prod/terraform.tfstate"' in example
    assert 'region       = "us-east-1"' in example
    assert "encrypt      = true" in example
    assert "assume_role = {" in example
    assert 'role_arn = "arn:aws:iam::610992396917:role/HyphaSynapseDeploymentRole"' in example
    assert "\nrole_arn     =" not in example
