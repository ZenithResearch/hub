# GitHub OIDC for non-deploying automation

GitHub Actions may use OIDC for bounded automation such as building and pushing immutable images. Production Terraform deploys are intentionally local/operator-controlled and are not run from GitHub Actions.

Do not store long-lived AWS access keys in GitHub secrets.

## GitHub configuration

Repository/environment variables used by image-building workflows:

- `AWS_REGION=us-east-1`
- `AWS_PROD_DEPLOY_ROLE_ARN=arn:aws:iam::<account-id>:role/<role-name>`

GitHub environment:

- `production`
- may require manual reviewers before image-build jobs assume the role.

No production tfvars secret is required for GitHub Actions. Production tfvars stay on the approved operator machine and are passed to `scripts/prod_terraform_cd.sh` during local plan/apply.

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

The current GitHub role should be scoped to the non-deploying action it supports:

- authenticate to ECR;
- push images to the intended Hub ECR repository such as `zenith-hub-prod-gateway-http`;
- read minimal account/region metadata needed by the workflow.

It should not need Terraform S3 backend, ECS service update, RDS, EFS, or broad production infrastructure mutation permissions while GitHub Actions CD is disabled.

## Deploy discipline

1. GitHub CI validates source.
2. The manual `Gateway Image` workflow may build and push an immutable image tag.
3. An operator inspects live service tags locally.
4. An operator runs `scripts/prod_terraform_cd.sh plan` locally with explicit service image tags and production tfvars from the approved local path.
5. The operator reviews the saved plan text.
6. The operator runs `scripts/prod_terraform_cd.sh apply` locally only after confirming the change window.
7. The operator waits for ECS stability and runs public/operator/internal smoke checks.
