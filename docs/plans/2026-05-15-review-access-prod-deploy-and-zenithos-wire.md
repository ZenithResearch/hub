# Review Access Production Deploy and ZenithOS Wire Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task when delegating. Current operator is allowed to execute the deployment steps directly after each verification gate.

**Goal:** Put the committed Hub Review Access backend (`1994d2d`) live on `hub.zenith-research.ca`, verify the admin rotation endpoint safely, then wire ZenithOS to use it without storing raw reviewer codes.

**Architecture:** Hub remains canonical for clients/projects/deployments/access-code hashes in private Postgres/RDS. AWS ECS injects secrets into `gateway-http`; ZenithOS calls the admin rotation endpoint (or a local wrapper around it) and persists only safe metadata. Raw generated reviewer codes are displayed/copied once and never logged or written to source.

**Tech Stack:** Hub FastAPI gateway, Terraform AWS baseline, ECS/Fargate, ECR, Secrets Manager, RDS Postgres, SwiftUI ZenithOS.

---

## Non-negotiables

- Never print raw reviewer access codes, deploy hook tokens, hashes, session tokens, DB passwords, or full DB URLs.
- Always use `AWS_PROFILE=zenith-hermes` and `AWS_REGION=us-east-1`.
- Confirm AWS account `044528206149` before writes.
- Public app/Vercel env gets only public IDs/URLs.
- Dan/SWRL reviewer access stays project-scoped (`deployment_id IS NULL`).
- Do not use S3 as a DB ferry.

---

## Task 1: Verify local checkpoint and prod baseline

**Objective:** Confirm the deploy source and target environment before any production write.

**Files:**
- Inspect: `<local hub repo path>`

**Commands:**

```bash
cd <local hub repo path>
git rev-parse --short HEAD
git status --short
AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 aws sts get-caller-identity --output json
curl -fsS https://hub.zenith-research.ca/health
terraform -chdir=infra/aws_baseline_80 validate
```

**Expected:** HEAD is `1994d2d` or descendant; account is `044528206149`; health is OK; Terraform validates.

---

## Task 2: Check Terraform production plan

**Objective:** Determine whether prod needs Terraform shape changes for the admin token/ECS env/RDS wiring.

**Commands:**

```bash
cd <local hub repo path>
AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 \
  terraform -chdir=infra/aws_baseline_80 plan -no-color
```

**Expected:** Plan includes only intended Hub infra changes. If the plan includes risky unrelated destructive changes, stop and report.

---

## Task 3: Ensure the operator admin token exists without printing it

**Objective:** Create or update the `review_access_admin_token` secret value safely.

**Command shape:**

```bash
# Do not print token. Generate in-process and write directly to Secrets Manager.
AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 python3 scripts/operator_set_review_access_admin_token.py
```

**If no helper exists:** use a short local script that generates a high-entropy token, writes it to Secrets Manager, and prints only `{ok, secret_id, version_id_present, secrets_printed:false}`.

**Expected:** Secret has an AWSCURRENT version; raw token never appears in terminal output.

---

## Task 4: Apply infra/task-definition changes

**Objective:** Make ECS inject `REVIEW_ACCESS_ADMIN_TOKEN` and run the committed gateway code.

**Commands:**

```bash
cd <local hub repo path>
AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 \
  terraform -chdir=infra/aws_baseline_80 apply -auto-approve
```

**Expected:** Apply succeeds. If Terraform needs an image tag update, build/push AMD64 images or do a gateway-only task definition redeploy as appropriate.

---

## Task 5: Build/push/deploy gateway image if needed

**Objective:** Ensure gateway-http runs code from commit `1994d2d` or descendant.

**Commands:**

```bash
TAG="review-access-$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD)"
REPO="044528206149.dkr.ecr.us-east-1.amazonaws.com/zenith-hub-prod-gateway-http"
AWS_PROFILE=zenith-hermes AWS_REGION=us-east-1 aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 044528206149.dkr.ecr.us-east-1.amazonaws.com >/dev/null

docker buildx build --platform linux/amd64 -t "$REPO:$TAG" --push .
```

Then register a new task definition by cloning the current gateway task definition and changing only the app image, preserving env/secrets/volumes/roles.

**Expected:** ECS service stabilizes on the new task definition.

---

## Task 6: Verify prod endpoint and redacted admin behavior

**Objective:** Confirm the admin rotation endpoint is live without leaking generated codes.

**Commands:**

```bash
curl -fsS https://hub.zenith-research.ca/openapi.json \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print("/v1/admin/review-auth/access-codes/rotate" in data.get("paths", {}))'
```

Then smoke missing-token behavior:

```bash
curl -sS -o /tmp/review-admin-no-token.json -w '%{http_code}\n' \
  -X POST https://hub.zenith-research.ca/v1/admin/review-auth/access-codes/rotate \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"probe","client_slug":"probe","client_name":"Probe","project_id":"swrl-ui","project_slug":"swrl-ui","project_name":"SWRL UI","access_code_id":"probe","access_label":"Probe","mode":"generate"}'
```

**Expected:** OpenAPI path exists; missing token returns `401`, not `404`/`503`.

---

## Task 7: Wire ZenithOS client call

**Objective:** Add a minimal Swift client for the admin rotation endpoint and connect the existing Review Access UI action to it.

**Files:**
- Modify/Create under `<local ZenithOS workspace>/Sources/ZenithOSUI/ReviewAccess/`
- Candidate new file: `ReviewAccessHubClient.swift`
- Modify: `ReviewAccessView.swift`

**Rules:**
- Admin token must come from local operator config/keychain/environment, not source.
- Persist only safe metadata.
- For generated mode, display/copy raw code once, then clear it from state on dismissal.

**Verification:**

```bash
cd <local ZenithOS workspace>
swift build -c debug --product ZenithOSUI
```

---

## Task 8: Final redacted smoke

**Objective:** Use ZenithOS or a CLI equivalent to rotate a test/probe access row and verify response shape without printing raw code.

**Expected safe output:**

```json
{
  "http_status": 200,
  "raw_code_present": true,
  "project_scoped_access": true,
  "secrets_printed": false
}
```

Do not print the raw code.

---

## Stop condition

Stop after prod Hub exposes and enforces the admin endpoint and ZenithOS builds with the new client wiring. Browser/SWRL end-to-end auth with a real Dan code is a separate reviewer-code rotation step and should not be done unless the raw code is available through a safe channel.
