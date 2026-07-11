# Matrix production evidence gate

This runbook is the repo-local operator surface for ISS-P14-007. It records how to collect evidence without committing secrets, tfvars, raw Terraform output, appservice tokens, or AWS session material.

## Scope

ISS-P14-007 unlocks P15 only after all of these are true:

1. Hub source is `main` or an issue branch at/after `aa1bd8c8050aacab11182f669085d3c23c7a60ff`.
2. The reviewed source includes `matrix_synapse_runtime.tf`, and the operator has populated the homeserver signing-key, macaroon, and controlled-registration secret handles without exposing values.
3. A first-phase production Terraform plan with `enable_matrix_synapse=true`, `enable_matrix_backup=true`, and `start_matrix_synapse_service=false` is captured, redacted, reviewed, and accepted before apply. Existing Hub services remain running. The existing HTTPS listener receives the Matrix host-routing rule in Phase 1 so ECS can associate the target group while the separate Matrix certificate attachment remains disabled until ACM is issued.
4. Apply evidence records the changed resources and confirms unrelated live service tags/state were preserved.
5. ECS target health plus public Matrix smoke verifies the production homeserver client path and federation port 8448.
6. Backup/restore evidence lists both tested restore paths and restore paths that remain explicitly unproven.

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

4. Verify required Matrix secret handles have current versions. Query metadata only; never print secret values:

```bash
for secret_id in \
  <name-prefix>/matrix/homeserver_signing_key \
  <name-prefix>/matrix/macaroon_secret_key \
  <name-prefix>/matrix/registration_shared_secret \
  <name-prefix>/matrix/form_secret; do
  aws secretsmanager describe-secret --secret-id "$secret_id" --query '{Name:Name,LastChangedDate:LastChangedDate}'
done
```

5. Run the production Terraform plan with redacted output paths. Never commit raw tfvars or raw plan text if it includes sensitive values. For the external DNS first phase, keep `matrix_hosted_zone_id` empty, `enable_matrix_https_listener=false`, `enable_matrix_federation=false`, and `start_matrix_synapse_service=false`; preserve current image tags for every unrelated service.

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

6. Review the plan resource list. It must include the Synapse ECS task/service at desired count zero, private RDS instance, encrypted EFS/access point/mount targets, target-group attachment, certificate, secret handles, and backup selection; it must not roll unrelated services backward. Only after acceptance, run apply. Add the emitted ACM validation CNAME and ALB host record at the external DNS provider, populate secret versions, then run a second accepted plan with `start_matrix_synapse_service=true`, `enable_matrix_https_listener=true`, and `enable_matrix_federation=true`.

7. Before accepting capacity, run 1,000 authenticated or public read-path requests with 10 concurrent clients. Acceptance requires less than 1% failures, p95 latency below 500 ms, ECS CPU and memory below 80%, RDS CPU below 80%, database connections below 70, and no EFS burst-credit alarm for the 15-minute observation window. The monolithic v0 topology is constrained to exactly one Synapse task; scaling above one task requires a reviewed Synapse worker architecture.

8. Verify outbound federation against a remote homeserver that resolves or delegates to port 8448, not only the local inbound listener. Record the remote host, resolved port, HTTP status, and timestamp without recording access tokens.

```bash
scripts/prod_terraform_cd.sh apply
```

7. Confirm ECS target health, then run public smokes:

```bash
curl -fsS https://synapse.zenith-research.ca/_matrix/client/versions
curl -fsS https://synapse.zenith-research.ca:8448/_matrix/federation/v1/version
```

8. Record backup/restore evidence. Restore the RDS and EFS recovery points to isolated non-production targets. If either path is not exercised, list it under `unproven_restore_paths`; #67 cannot close with no tested database and media restore path.

9. Validate the redacted evidence JSON:

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
