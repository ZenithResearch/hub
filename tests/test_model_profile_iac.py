from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gateway_task_persists_model_profile_override_and_audit_paths() -> None:
    ecs = (ROOT / "infra/aws_baseline_80/ecs.tf").read_text()
    variables = (ROOT / "infra/aws_baseline_80/variables.tf").read_text()
    tfvars_example = (ROOT / "infra/aws_baseline_80/terraform.tfvars.example").read_text()

    assert 'name = "MODEL_PROFILES_PATH"' in ecs
    assert 'value = var.gateway_model_profiles_path' in ecs
    assert 'name = "MODEL_PROFILE_OVERRIDES_PATH"' in ecs
    assert 'value = var.gateway_model_profile_overrides_path' in ecs
    assert 'name = "MODEL_PROFILE_AUDIT_PATH"' in ecs
    assert 'value = var.gateway_model_profile_audit_path' in ecs

    assert 'variable "gateway_model_profiles_path"' in variables
    assert 'default     = "infra/model-profiles.yaml"' in variables
    assert 'variable "gateway_model_profile_overrides_path"' in variables
    assert 'default     = "/data/model-profile-overrides.yaml"' in variables
    assert 'variable "gateway_model_profile_audit_path"' in variables
    assert 'default     = "/data/model-profile-audit.jsonl"' in variables

    assert 'gateway_model_profiles_path = "infra/model-profiles.yaml"' in tfvars_example
    assert 'gateway_model_profile_overrides_path = "/data/model-profile-overrides.yaml"' in tfvars_example
    assert 'gateway_model_profile_audit_path = "/data/model-profile-audit.jsonl"' in tfvars_example


def test_gateway_security_group_can_reach_private_llama_server() -> None:
    security_groups = (ROOT / "infra/aws_baseline_80/security_groups.tf").read_text()
    gateway_block = security_groups.split('resource "aws_security_group" "gateway" {', 1)[1].split(
        'resource "aws_security_group" "runtime" {', 1
    )[0]

    assert 'description = "gateway_to_llama_server_openai"' in gateway_block
    assert "from_port   = 3690" in gateway_block
    assert "to_port     = 3690" in gateway_block


def test_ci_runs_project_h_static_checks() -> None:
    ci_script = (ROOT / "scripts/ci_check.sh").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert 'scripts/model_profile_check.py' in ci_script
    assert 'scripts/matrix_deployment_check.py' in ci_script
    assert 'scripts/deployment_profile_check.py' in ci_script
    assert 'scripts/external_root_check.py' in ci_script
    assert 'Run baseline CI checks' in workflow
