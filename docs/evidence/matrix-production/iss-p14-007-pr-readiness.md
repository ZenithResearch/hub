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

## Accepted production status

PR #86 and its Phase 1 correction PR #87 are merged. The two-phase production rollout completed from reviewed `main`: inactive infrastructure first, then DNS/ACM, confirmed alarm routing, populated secret versions, backups, and explicit one-task activation. The redacted accepted artifacts are:

- `iss-p14-007-plan-redacted.txt`
- `iss-p14-007-apply-redacted.txt`
- `iss-p14-007-production.json`

Live client/federation smoke, a 1,000-request capacity pass, fifteen-minute infrastructure metrics, both backup jobs, isolated RDS/EFS restore jobs, and a temporary private Synapse readiness check all passed. Temporary restore resources were removed after acceptance. The evidence validator is the source of truth for unlocking P15.

## Non-claims preserved

- Production claims are limited to the checks recorded in the validated redacted artifact.
- No raw Matrix/admin/appservice/AWS/tfvars material is committed.
- Matrix identity remains provenance/context only, not Hub authority.
- P15 is unlocked by the validated accepted plan/apply/smoke/backup gates; P15 retains its own appservice and delivery acceptance criteria.
