# ISS-P14-007 PR readiness evidence

## Source

- Branch: `issue/iss-p14-007-production-plan-apply-smoke-evidence`
- Minimum source commit required by the evidence gate: `aa1bd8c8050aacab11182f669085d3c23c7a60ff`
- PR scope: repo-local production evidence gate, validator, template, and operator runbook for Synapse production plan/apply + smoke + backup/restore evidence.

## Verification run before PR

```bash
python3 scripts/matrix_production_evidence_check.py --help
python3 scripts/matrix_production_evidence_check.py validate docs/evidence/matrix-production/iss-p14-007-template.json
uv run --with pytest pytest -q tests/matrix/test_iss_p14_007_production_evidence.py
python3 -m py_compile scripts/matrix_production_evidence_check.py
scripts/private_artifact_scan.py
git diff --check
```

## Operator-auth status

AWS SSO was requested for the live production plan/apply evidence run, but the device-code session expired before approval. Therefore this PR does not claim live production apply acceptance. It provides the tested evidence gate and keeps the operator runbook explicit that P15 remains locked until a later operator-authenticated run records accepted redacted production evidence.

## Non-claims preserved

- No production deployment is claimed by this PR alone.
- No raw Matrix/admin/appservice/AWS/tfvars material is committed.
- Matrix identity remains provenance/context only, not Hub authority.
- P15 remains blocked unless the validated evidence artifact records accepted plan/apply/smoke/backup gates.
