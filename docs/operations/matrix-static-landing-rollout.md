# Zenith Synapse static landing rollout

This runbook publishes the reviewed static page through the hardened Synapse image. It does not change registration, authentication, Matrix APIs, federation, database state, or Hub authority. Run it only from reviewed, green `main`.

## 1. Build and review the candidate

Dispatch the `Hardened Synapse Image` workflow (`.github/workflows/synapse-image.yml`) with the `workflow_dispatch` event and an immutable `matrix-synapse-...` tag. The verify job must build the Linux image, exercise the runtime, compare the installed static-page SHA-256 with `infra/matrix/synapse/static/index.html`, and pass the blocking Trivy HIGH/CRITICAL scan before publish can run.

```bash
gh workflow run synapse-image.yml --ref main -f image_tag="matrix-synapse-$(git rev-parse --short=12 HEAD)"
gh run list --workflow synapse-image.yml --branch main --limit 5
```

Stop on any failed build, byte comparison, runtime exercise, or Trivy result.

## 2. Resolve and scan the exact published digest

Record the publish job's `imageDigest`, then independently resolve the tag in ECR. The two digests must match. Use the digest-pinned form (`...@sha256:...`) for every subsequent scan and deployment input; never deploy the mutable tag.

```bash
export AWS_PROFILE=zenith-hermes
export AWS_REGION=us-east-1
export ECR_REPOSITORY=zenith-hub-prod-runtime-grpc
export IMAGE_TAG=matrix-synapse-<commit>
export IMAGE_DIGEST="$(aws ecr describe-images \
  --repository-name "$ECR_REPOSITORY" \
  --image-ids imageTag="$IMAGE_TAG" \
  --query 'imageDetails[0].imageDigest' --output text)"
export ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export MATRIX_IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY@$IMAGE_DIGEST"
test "${IMAGE_DIGEST#sha256:}" != "$IMAGE_DIGEST"
```

Scan that exact `$MATRIX_IMAGE` with the approved Trivy image scanner and stop on fixable HIGH/CRITICAL findings. Preserve the workflow URL, `imageDigest`, digest-pinned image reference, and scan result in the production evidence packet without credentials.

## 3. Plan a Synapse-only rollout

Set the production Terraform `matrix_synapse_image` input to `$MATRIX_IMAGE`. Preserve every other live image and production input. Generate and inspect a saved `terraform plan` from the normal production backend.

The plan must change only the Synapse task definition and ECS service (`zenith-hub-prod-matrix-synapse`). It must contain no RDS, EFS, database, secret, Gateway, Frank, Cases, or Eventbus changes. Stop if the plan contains any unrelated change.

```bash
terraform -chdir=infra/aws_baseline_80 plan \
  -var="matrix_synapse_image=$MATRIX_IMAGE" \
  -out=/tmp/issue-91-synapse.tfplan
terraform -chdir=infra/aws_baseline_80 show -no-color /tmp/issue-91-synapse.tfplan
```

After operator review, run `terraform apply` on the saved plan. Do not use a direct ECS update that bypasses the reviewed Terraform input.

```bash
terraform -chdir=infra/aws_baseline_80 apply /tmp/issue-91-synapse.tfplan
aws ecs wait services-stable --cluster zenith-hub-prod-cluster \
  --services zenith-hub-prod-matrix-synapse
```

## 4. Live smoke

Verify the page response and body separately:

```bash
curl -fsS -D /tmp/zenith-matrix-static.headers \
  -o /tmp/zenith-matrix-static.html \
  https://synapse.zenith-research.ca/_matrix/static/
grep -Ei '^HTTP/2 200|^content-type: text/html' /tmp/zenith-matrix-static.headers
grep -F 'Zenith Matrix is running' /tmp/zenith-matrix-static.html
curl -fsS https://synapse.zenith-research.ca/_matrix/client/versions
curl -fsS https://synapse.zenith-research.ca:8448/_matrix/federation/v1/version
```

Expected landing response: `HTTP/2 200`, `content-type: text/html`, and the locked headline. The client versions and federation endpoints must remain green.

Open `https://synapse.zenith-research.ca/_matrix/static/` in a browser. Check desktop and a 320 px viewport, keyboard-tab focus on both links, correct destinations, readable non-overflowing content, and an empty error console. Confirm no script, analytics, cookie, font, or image request is emitted.

## 5. Rollback

If ECS health, the landing page, client versions, federation, or browser checks regress, stop rollout evidence and rollback by restoring the previously recorded digest-pinned `matrix_synapse_image`. Generate and review a fresh Synapse-only plan, run `terraform apply`, wait for `zenith-hub-prod-matrix-synapse` stability, and repeat all live smokes. Do not modify durable Matrix state as part of image rollback.
