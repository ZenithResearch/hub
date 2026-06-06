# Matrix production evidence gate

This runbook is the repo-local operator surface for ISS-P14-007. It records how to collect evidence without committing secrets, tfvars, raw Terraform output, appservice tokens, or AWS session material.

## Scope

ISS-P14-007 unlocks P15 only after all of these are true:

1. Hub source is `main` or an issue branch at/after `aa1bd8c8050aacab11182f669085d3c23c7a60ff`.
2. A production Terraform plan is captured, redacted, reviewed, and accepted before apply.
3. Apply evidence records the changed resources and confirms unrelated live service tags/state were preserved.
4. Public Matrix smoke verifies the production homeserver client path and federation port 8448.
5. Backup/restore evidence lists both tested restore paths and restore paths that remain explicitly unproven.

If any gate is pending, downstream P15 must remain locked.

## Evidence workflow

1. Start from a clean checkout:

```bash
git status --short --branch
git rev-parse HEAD
```

2. Authenticate as the operator. Do not paste SSO/session/token output into logs or evidence.

```bash
AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 aws sso login
AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json
```

3. Inspect live service tags and copy only image tags/service names into redacted evidence. Preserve unrelated Gateway/Eventbus/Cases/Frank/STT tags unless the accepted plan intentionally changes them.

4. Run the production Terraform plan with redacted output paths. Never commit raw tfvars or raw plan text if it includes sensitive values.

```bash
export AWS_PROFILE=zenith-hermes
export AWS_REGION=us-east-1
export PROD_TFVARS_PATH=/path/to/operator/terraform.tfvars
export TERRAFORM_PLAN_PATH=/tmp/iss-p14-007.tfplan
export TERRAFORM_PLAN_TEXT=/tmp/iss-p14-007-plan.txt
export GATEWAY_IMAGE_TAG=<current-live-gateway-tag>
export EVENTBUS_IMAGE_TAG=<current-live-eventbus-tag>
export CASES_IMAGE_TAG=<current-live-cases-tag>
export FRANK_IMAGE_TAG=<current-live-frank-tag>
export STT_IMAGE_TAG=<current-live-stt-tag>
scripts/prod_terraform_cd.sh plan
```

5. Review the plan resource list. Only after acceptance, run apply:

```bash
scripts/prod_terraform_cd.sh apply
```

6. Run public smokes:

```bash
curl -fsS https://synapse.zenith-research.ca/_matrix/client/versions
nc -vz synapse.zenith-research.ca 8448
```

7. Record backup/restore evidence. Use non-production restore targets for restore proof. If a restore path is not exercised, list it under `unproven_restore_paths`.

8. Validate the redacted evidence JSON:

```bash
python3 scripts/matrix_production_evidence_check.py validate docs/evidence/matrix-production/iss-p14-007-template.json
```

## Redaction rules

Reject and re-create the evidence if it contains:

- `terraform.tfvars` contents or paths used as evidence artifacts.
- raw `as_token`, `hs_token`, registration, macaroon, signing key, bearer token, or `rev_...` material.
- unredacted SSO/session data.
- any claim that P15 is unlocked while plan/apply/smoke/backup evidence is pending.

## Current operator-auth note

If AWS SSO is unavailable or expired, this issue branch can still ship the evidence gate and tests, but it must not claim live production apply acceptance. In that case, leave P15 locked until an operator reruns the workflow and replaces placeholder evidence with accepted redacted evidence.
