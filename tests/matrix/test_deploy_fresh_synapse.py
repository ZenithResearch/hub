import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_fresh_synapse.py"


def load_deployer():
    spec = importlib.util.spec_from_file_location("deploy_fresh_synapse", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_is_single_command_configured_and_secret_safe():
    source = SCRIPT.read_text(encoding="utf-8")
    for marker in [
        'EXPECTED_PROFILE = "zenith-hypha-free"',
        'EXPECTED_DEPLOYMENT_PROFILE = "zenith-hypha-synapse"',
        'EXPECTED_ACCOUNT = "610992396917"',
        'EXPECTED_REGION = "us-east-1"',
        '"bootstrap_fresh_synapse_account.py"',
        '"populate_fresh_synapse_secret.py"',
        '"terraform", "init"',
        '"terraform", "plan"',
        '"terraform", "apply"',
        '"terraform", "state", "list"',
        "if needs_base_stage(state):",
        "runtime_verification_commands()",
        '"ssm",\n                "send-command"',
        '"get-command-invocation"',
        '"secretsmanager",\n            "describe-secret"',
        '"AWSCURRENT"',
        '"dns": {"type": "A"',
    ]:
        assert marker in source
    for forbidden in [
        "get-secret-value",
        "SecretString",
        "provision_fresh_synapse_admin.py",
        "@beaver:",
        "terraform import",
        "-auto-approve",
    ]:
        assert forbidden not in source


def test_plan_validator_allows_only_exact_creates_and_noop():
    deployer = load_deployer()
    plan = {
        "resource_changes": [
            {"address": "aws_vpc.matrix", "change": {"actions": ["create"]}},
            {"address": "aws_subnet.matrix", "change": {"actions": ["no-op"]}},
        ]
    }
    assert deployer.validate_plan(plan, {"aws_vpc.matrix"}) == {
        "aws_vpc.matrix": ["create"]
    }


@pytest.mark.parametrize("actions", [["update"], ["delete"], ["create", "delete"], ["import"]])
def test_plan_validator_rejects_mutation_outside_exact_create(actions):
    deployer = load_deployer()
    plan = {"resource_changes": [{"address": "aws_vpc.matrix", "change": {"actions": actions}}]}
    with pytest.raises(deployer.DeploymentError):
        deployer.validate_plan(plan, {"aws_vpc.matrix"})


def test_plan_validator_rejects_unexpected_resource():
    deployer = load_deployer()
    plan = {
        "resource_changes": [
            {"address": "aws_db_instance.unexpected", "change": {"actions": ["create"]}}
        ]
    }
    with pytest.raises(deployer.DeploymentError):
        deployer.validate_plan(plan, deployer.BASE_CREATES)


def test_partial_base_state_is_completed_before_runtime_activation():
    deployer = load_deployer()

    assert deployer.needs_base_stage([])
    assert deployer.needs_base_stage(["aws_secretsmanager_secret.matrix"])
    assert not deployer.needs_base_stage(["aws_instance.matrix[0]"])
    assert not deployer.needs_base_stage(["aws_eip.matrix[0]"])


def test_runtime_acceptance_requires_mount_containers_and_internal_synapse():
    deployer = load_deployer()
    commands = "\n".join(deployer.runtime_verification_commands())

    for marker in [
        "cloud-init status --wait",
        "findmnt -n -o FSTYPE /opt/matrix-data",
        "findmnt -n -o LABEL /opt/matrix-data",
        "systemctl is-active docker",
        "matrix-db",
        "matrix-synapse",
        "/_matrix/client/versions",
        "for attempt in $(seq 1 120)",
        "sleep 5",
        "runtime did not become healthy",
    ]:
        assert marker in commands
