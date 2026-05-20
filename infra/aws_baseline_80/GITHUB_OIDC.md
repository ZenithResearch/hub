# GitHub OIDC production deploy role

Project E production CD uses GitHub Actions OIDC. Do not store long-lived AWS access keys in GitHub secrets.

## GitHub configuration

Repository/environment variables:

- `AWS_REGION=us-east-1`
- `AWS_PROD_DEPLOY_ROLE_ARN=arn:aws:iam::<account-id>:role/<role-name>`

Repository/environment secrets:

- `PROD_TERRAFORM_TFVARS_B64` — base64-encoded production `infra/aws_baseline_80/terraform.tfvars`.

GitHub environment:

- `production`
- require manual reviewers before allowing the workflow job to start.

## Trust policy shape

Restrict the role to this repository and the `production` environment. Replace account/repo details before applying.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:ZenithResearch/hub:environment:production"
        }
      }
    }
  ]
}
```

If a separate staging environment is added, create a separate staging role and environment rather than widening the production subject.

## Permission policy shape

Start narrow and expand only when the workflow proves it needs more. The Project E workflow needs enough access to:

- read/write the Terraform S3 backend object and lockfile for `aws_baseline_80/terraform.tfstate`;
- describe and update Terraform-managed resources in the Hub production stack;
- read/write ECR images only when later image-build deployment is enabled;
- run `scripts/prod_smoke.py` public smoke without credentials, and later operator/internal smoke when intentionally enabled.

Initial Terraform-management policy should be reviewed against the generated plan before first apply. Prefer resource ARNs scoped to:

- state bucket: `zenith-hub-tf-state-044528206149-us-east-1`;
- Hub prod cluster/resources named `zenith-hub-prod-*`;
- ECR repositories managed by `infra/aws_baseline_80`;
- S3 model bucket `zenith-hub-prod-llama-models-044528206149-us-east-1` only if model staging is moved into CD;
- CloudWatch log groups `/ecs/zenith-hub-prod/*`.

## Apply discipline

Use the workflow in this order:

1. Run `smoke`.
2. Run `plan` and download/review the plan artifact.
3. Run `apply` only after review, with exact confirmation `APPLY zenith-hub-prod` and GitHub environment approval.
4. Check post-apply smoke output.
5. If smoke fails, roll back to the prior image tag/task definition or revert/apply the prior Terraform commit.
