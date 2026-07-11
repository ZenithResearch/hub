# ISS-P14-007 PR readiness evidence

## Source

- Evidence-gate branch: `issue/iss-p14-007-production-plan-apply-smoke-evidence` (merged via PR #68)
- Live-target follow-up branch: `issue/67-live-synapse-target`
- Minimum source commit required by the evidence gate: `aa1bd8c8050aacab11182f669085d3c23c7a60ff`
- PR scope: repo-local production evidence gate, validator, template, and operator runbook for Synapse production plan/apply + smoke + backup/restore evidence.
- Follow-up scope: private ECS/Fargate Synapse target, private RDS Postgres, encrypted EFS state, ALB attachment, and concrete AWS Backup selection.

## Verification run before PR

```bash
python3 scripts/matrix_production_evidence_check.py --help
python3 scripts/matrix_production_evidence_check.py validate docs/evidence/matrix-production/iss-p14-007-template.json
uv run --with pytest pytest -q tests/matrix/test_iss_p14_007_production_evidence.py
python3 -m pytest -q tests/matrix/test_iss_p14_007_live_synapse_target.py
terraform -chdir=infra/aws_baseline_80 validate -no-color
python3 -m py_compile scripts/matrix_production_evidence_check.py
scripts/private_artifact_scan.py
git diff --check
```

## Operator-auth status

The PR #68 operator run proved AWS/backend access but correctly rejected apply because there was no live Synapse target. The follow-up PR supplies that target fail-closed by default. It still does not claim production apply acceptance: apply, smoke, and isolated restore evidence occur only after review/merge and an accepted operator plan.

On 2026-07-10, AWS SSO/backend access was re-established and a targeted first-phase plan was generated from PR #86 with current live image tags, the live clients Postgres engine version, `enable_matrix_synapse=true`, `enable_matrix_backup=true`, and `start_matrix_synapse_service=false`. The reviewed plan contains 25 creates and two intended updates only: Matrix/Synapse compute, RDS, EFS, certificate, secret handles, backup resources, IAM, target group/security groups, plus the existing execution-secret policy and ALB egress. It contains no unrelated service rollout, database engine change, task-definition replacement, or service scale-down. The plan was not applied because PR #86 remains under review and unmerged.

## Non-claims preserved

- No production deployment is claimed by this PR alone.
- No raw Matrix/admin/appservice/AWS/tfvars material is committed.
- Matrix identity remains provenance/context only, not Hub authority.
- P15 remains blocked unless the validated evidence artifact records accepted plan/apply/smoke/backup gates.
