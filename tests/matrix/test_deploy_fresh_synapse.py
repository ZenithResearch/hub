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
        'if "aws_secretsmanager_secret.matrix" not in state:',
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
