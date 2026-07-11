import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK_SCRIPT = ROOT / "scripts" / "matrix_production_evidence_check.py"
TEMPLATE = ROOT / "docs" / "evidence" / "matrix-production" / "iss-p14-007-template.json"
MIN_SOURCE_HEAD = "aa1bd8c"


def valid_evidence() -> dict:
    return {
        "issue": "ISS-P14-007",
        "source": {
            "head": MIN_SOURCE_HEAD,
            "branch": "issue/iss-p14-007-production-plan-apply-smoke-evidence",
        },
        "terraform": {
            "plan": {
                "status": "accepted",
                "artifact": "evidence/terraform-plan-redacted.txt",
                "reviewed_by": "hub-operator",
            },
            "apply": {
                "status": "accepted",
                "artifact": "evidence/terraform-apply-redacted.txt",
                "reviewed_by": "hub-operator",
            },
        },
        "public_smoke": {
            "status": "accepted",
            "client_api": {
                "url": "https://matrix.example.test/_matrix/client/versions",
                "status": 200,
            },
            "federation_8448": {
                "host": "matrix.example.test",
                "port": 8448,
                "status": 200,
            },
        },
        "backup_restore": {
            "status": "accepted",
            "rpo_target_hours": 24,
            "rds_recovery_point_age_hours": 1.5,
            "efs_recovery_point_age_hours": 2.0,
            "rto_target_minutes": 120,
            "start_to_usable_minutes": 45,
            "restore_jobs": {
                "rds": "COMPLETED",
                "efs": "COMPLETED",
            },
            "tested_restore_paths": [
                "postgres database recovery point restored to non-production target",
                "media store recovery point restored to non-production target",
            ],
            "unproven_restore_paths": [
                "homeserver signing key rotation after restore",
                "appservice token rotation during restore",
            ],
        },
        "downstream": {
            "p15": {
                "status": "unlocked",
                "reason": "plan, apply, smoke, and backup evidence accepted",
            }
        },
    }


def run_check(tmp_path: Path, evidence: dict) -> subprocess.CompletedProcess[str]:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), "validate", str(evidence_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}".lower()


def assert_rejected(result: subprocess.CompletedProcess[str], *needles: str) -> None:
    assert result.returncode != 0, result.stdout + result.stderr
    output = combined_output(result)
    for needle in needles:
        assert needle.lower() in output


def assert_accepted(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    assert "accepted" in combined_output(result)


def test_template_exists_and_documents_required_evidence_sections():
    assert TEMPLATE.exists(), f"missing evidence template: {TEMPLATE}"
    template = json.loads(TEMPLATE.read_text())
    for key in [
        "issue",
        "source",
        "terraform",
        "public_smoke",
        "backup_restore",
        "downstream",
    ]:
        assert key in template
    assert template["issue"] == "ISS-P14-007"
    assert template["source"]["minimum_head"] == MIN_SOURCE_HEAD


def test_valid_production_evidence_is_accepted_and_unlocks_p15(tmp_path):
    result = run_check(tmp_path, valid_evidence())
    assert_accepted(result)
    assert "p15" in combined_output(result)
    assert "unlocked" in combined_output(result)


def test_source_head_must_be_at_or_after_minimum_required_commit(tmp_path):
    evidence = valid_evidence()
    evidence["source"]["head"] = "aa1bd8b"

    result = run_check(tmp_path, evidence)

    assert_rejected(result, "source", "head", MIN_SOURCE_HEAD)


def test_apply_evidence_is_rejected_until_plan_is_accepted(tmp_path):
    evidence = valid_evidence()
    evidence["terraform"]["plan"]["status"] = "pending"
    evidence["terraform"]["apply"]["status"] = "accepted"

    result = run_check(tmp_path, evidence)

    assert_rejected(result, "plan", "accepted", "apply")


def test_apply_evidence_is_rejected_when_plan_section_is_missing(tmp_path):
    evidence = valid_evidence()
    del evidence["terraform"]["plan"]

    result = run_check(tmp_path, evidence)

    assert_rejected(result, "plan", "apply")


def test_sensitive_tokens_tfvars_and_raw_secrets_are_rejected(tmp_path):
    evidence = valid_evidence()
    evidence["terraform"]["plan"]["artifact"] = "terraform.tfvars"
    evidence["leaked_material"] = {
        "as_token": "raw-as-token-value",
        "hs_token": "raw-hs-token-value",
        "registration_shared_secret": "raw-registration-secret",
        "macaroon_secret_key": "raw-macaroon-secret",
        "raw_secret": "super-secret-production-value",
    }

    result = run_check(tmp_path, evidence)

    assert_rejected(result, "sensitive", "tfvars", "token", "raw")


def test_public_smoke_requires_client_api_and_federation_8448_status(tmp_path):
    evidence = valid_evidence()
    del evidence["public_smoke"]["client_api"]
    evidence["public_smoke"]["federation_8448"]["status"] = 0

    result = run_check(tmp_path, evidence)

    assert_rejected(result, "client", "federation", "8448")


def test_public_smoke_rejects_non_success_client_api_status(tmp_path):
    evidence = valid_evidence()
    evidence["public_smoke"]["client_api"]["status"] = 503

    result = run_check(tmp_path, evidence)

    assert_rejected(result, "client", "status")


def test_backup_restore_requires_tested_and_unproven_restore_paths(tmp_path):
    evidence = valid_evidence()
    evidence["backup_restore"]["tested_restore_paths"] = []
    evidence["backup_restore"].pop("unproven_restore_paths")

    result = run_check(tmp_path, evidence)

    assert_rejected(result, "tested", "unproven", "restore")


def test_backup_restore_rejects_missed_rpo_rto_or_incomplete_jobs(tmp_path):
    evidence = valid_evidence()
    evidence["backup_restore"]["rds_recovery_point_age_hours"] = 25
    evidence["backup_restore"]["start_to_usable_minutes"] = 121
    evidence["backup_restore"]["restore_jobs"]["efs"] = "FAILED"

    result = run_check(tmp_path, evidence)

    assert_rejected(result, "rpo", "rto", "restore", "completed")


def test_downstream_p15_remains_locked_unless_all_required_gates_are_accepted(tmp_path):
    for gate_path in [
        ("terraform", "plan"),
        ("terraform", "apply"),
        ("public_smoke",),
        ("backup_restore",),
    ]:
        evidence = valid_evidence()
        target = evidence
        for key in gate_path:
            target = target[key]
        target["status"] = "pending"
        evidence["downstream"]["p15"]["status"] = "unlocked"

        result = run_check(tmp_path, evidence)

        assert_rejected(result, "p15", "locked", "accepted")


def test_downstream_p15_locked_state_is_accepted_while_evidence_is_incomplete(tmp_path):
    evidence = valid_evidence()
    evidence["public_smoke"]["status"] = "pending"
    evidence["downstream"]["p15"] = {
        "status": "locked",
        "reason": "public smoke is not accepted yet",
    }

    result = run_check(tmp_path, evidence)

    assert_accepted(result)
    assert "locked" in combined_output(result)
