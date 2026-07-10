# ISS-P14-007: Production plan/apply and smoke evidence for Synapse core

> Issue #67 remains the operational completion gate. PR #68 created the evidence validator; the current follow-up PR adds the missing production-owned Synapse compute/data target. Neither PR alone unlocks P15: production apply, public smoke, and restore evidence still require an operator-authenticated post-merge run.

## PR boundary

- **PR scope:** ISS-P14-007: Production plan/apply and smoke evidence for Synapse core
- **Current branch:** `issue/67-live-synapse-target`
- **Current PR title:** `ISS-P14-007: Add the production Synapse target`
- **Primary repo:** `ZenithResearch/hub`
- **GitHub issue:** https://github.com/ZenithResearch/hub/issues/67
- **Source base:** Hub `main` at or after `aa1bd8c8050aacab11182f669085d3c23c7a60ff`

## Objective

Provide both the auditable evidence gate and a concrete, private production target for Synapse before P15 appservice smokes proceed. The target is a single-worker ECS/Fargate service backed by private RDS Postgres and encrypted EFS media/config state, attached to the existing Matrix ALB target group.

## Current preflight state

- Source-control gate is complete: P14 PR #57/#58/#59 landed, and Phase 0 cleanup landed via Hub #65/#66, ZenithOS #6/#8, and zenith-hub #6.
- PR #68 merged the redacted evidence validator and operator runbook. Its operator plan correctly stopped because the plan contained no live Synapse compute/data target.
- The follow-up branch adds that missing target behind `enable_matrix_synapse = false`; required secret handles and an accepted operator plan are prerequisites to enabling it.
- Production evidence is not complete until an operator-authenticated run records an accepted plan, apply result, public smoke, and backup/restore scope.
- AWS SSO/operator authentication is required for the live plan/apply steps; no committed file may contain tfvars, tokens, or raw Terraform output that includes sensitive values.

## Acceptance criteria

- Terraform plan evidence is captured from Hub `main` at or after `aa1bd8c` and reviewed/accepted before apply.
- Apply evidence records exactly what changed and preserves unrelated live service tags/state.
- Public Matrix smoke proves the intended production homeserver path responds.
- Backup/restore evidence documents what was tested and explicitly marks untested restore paths as unproven.
- Master DAG/checklist and P15 notes are updated before this issue is marked complete.

## Evidence artifact contract

Operator runbook: `docs/operations/matrix-production-evidence.md`.


The evidence artifact is JSON, generated/validated by `scripts/matrix_production_evidence_check.py`. It must include:

- `issue`: `ISS-P14-007`
- `source`: `repo`, `branch`, `head_sha`, `minimum_head_sha`
- `plan`: `status`, `accepted`, `summary`, `preserved_service_tags`, `redactions`
- `apply`: `status`, `reviewed_plan_sha256`, `summary`, `changed_resources`, `preserved_service_tags`
- `smoke`: `status`, `homeserver`, `client_api`, `federation_8448`, `checked_at`
- `backup_restore`: `status`, `tested`, `untested`, `unproven_restore_paths`
- `downstream`: `p15_unlocked`, `notes`

## Verification commands

```bash
python3 scripts/matrix_production_evidence_check.py --help
python3 scripts/matrix_production_evidence_check.py validate docs/evidence/matrix-production/iss-p14-007-template.json
python3 -m pytest tests/matrix/test_iss_p14_007_production_evidence.py -q
python3 -m pytest tests/matrix/test_iss_p14_007_live_synapse_target.py -q
terraform -chdir=infra/aws_baseline_80 validate -no-color
git diff --check
```

## Forbidden claims

- Do not claim Synapse is production-deployed unless apply evidence exists.
- Do not claim Synapse is production-ready until public smoke and backup/restore evidence are captured.
- Do not print or persist raw appservice/admin/reviewer secrets, tfvars, SSO tokens, appservice tokens, signing keys, or Terraform output containing sensitive values.
- Do not proceed to P15 production `whoami`/delivery smokes before this evidence gate is accepted.
- Do not introduce wallet/secS-magik/Review SDK auth into this v0 Matrix/Synapse issue set.

## Task / commit boundaries

1. Scope and baseline evidence.
2. Contract / failing evidence tests.
3. Implement production evidence validator.
4. Negative cases and edge behavior.
5. Docs, operator notes, and evidence hooks.
6. PR readiness verification.
7. DAG/checklist handoff evidence.
8. Final issue/PR evidence sync.

## PR evidence sync

The PR branch is intentionally task-per-commit:

1. `53d78a8` — scope and baseline evidence.
2. `e44b604` — contract tests for the evidence gate.
3. `bfa6579` — primary evidence validator/template implementation.
4. `bc292e3` — edge-case validation hardening.
5. `6022b3e` — operator runbook and evidence hooks.
6. `e88a5a3` — PR readiness verification evidence.
7. `045b7d8` — downstream P15 gate/checklist handoff.
8. Final commit — issue/PR sync and non-claim preservation.

PR #68 completed the evidence-gate commits above. The follow-up target PR uses separate commits for runtime infrastructure/tests, operator surfaces, and final verification. Live operator plan/apply evidence remains a post-merge gate and must be recorded through the validated evidence artifact before P15 is treated as production-unlocked.
