# Setup Guide (GitHub + AWS)

This guide captures the end-to-end setup flow for this repo:

- Multi-account GitHub authentication (SSH)
- Local dev (Docker Compose)
- AWS deploy (ECS/Fargate + ALB, optional CloudFront/WAF) using the repo Make targets

If you only want infrastructure options, start at [`infra/README.md`](../infra/README.md).

---

## Prerequisites

### macOS (Homebrew)

```bash
brew install git awscli
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
brew install --cask docker
```

Verify:

```bash
docker --version
docker compose version
aws --version
terraform version
```

---

## GitHub: authenticate another profile (SSH, recommended)

If you use SSH remotes (`git@github.com:ORG/REPO.git`), you do **not** need `gh` installed.
For multiple GitHub accounts, use **one SSH key per account** + **host aliases**.

### Optional: GitHub CLI (`gh`)

`gh` is useful for PRs/issues and HTTPS auth, but it’s **not required** for SSH-based `git push/pull`.

Install and log in:

```bash
brew install gh
gh auth login
```

If you add multiple accounts, you can switch:

```bash
gh auth switch -u OTHER_USERNAME
```

### 1) Create a new key for the other account

```bash
ssh-keygen -t ed25519 -C "personal@example.com" -f ~/.ssh/id_ed25519_personal
eval "$(ssh-agent -s)"
ssh-add --apple-use-keychain ~/.ssh/id_ed25519_personal
# If `--apple-use-keychain` isn't supported on your macOS version, try:
# ssh-add -K ~/.ssh/id_ed25519_personal
```

### 2) Add the public key to GitHub (the other account)

```bash
pbcopy < ~/.ssh/id_ed25519_personal.pub
```

GitHub → Settings → SSH and GPG keys → New SSH key:

- **Key type**: Authentication Key
- **Title**: something like `personal-mbp-2026`
- **Key**: paste the public key (starts with `ssh-ed25519 ...`)

### 3) Add a host alias in `~/.ssh/config`

Create/edit:

```bash
mkdir -p ~/.ssh
vim ~/.ssh/config
chmod 700 ~/.ssh
chmod 600 ~/.ssh/config
```

Add:

```sshconfig
Host github.com-personal
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_personal
  IdentitiesOnly yes
  AddKeysToAgent yes
  UseKeychain yes
```

### 4) Test

```bash
ssh -T git@github.com-personal
```

### 5) Use the alias in a repo remote

```bash
git remote set-url origin git@github.com-personal:OWNER/REPO.git
```

---

## Local dev (Docker Compose)

```bash
cp .env.example .env
make up
```

Verify:

```bash
curl -sS http://localhost:8080/health
```

Optional seed:

```bash
make seed
```

---

## AWS deploy (Edge default): ECS/Fargate + ALB + (optional) CloudFront/WAF

Docs:
- Overview: [`infra/aws/README.md`](../infra/aws/README.md)
- Terraform: [`infra/aws/terraform/`](../infra/aws/terraform/)

### 0) AWS credentials

For the fastest “it works” path while you’re learning, use an AWS principal with broad permissions
(commonly **`AdministratorAccess`**) so Terraform can create the needed resources.

If using an IAM user with access keys:

```bash
aws configure --profile your-profile
aws sts get-caller-identity --profile your-profile
```

### 1) Create `.env.local`

Copy the template and edit values:

```bash
cp .env.local.example .env.local
```

Key variables:

- **`AWS_PROFILE`**: the AWS CLI profile name to use (optional)
- **`AWS_REGION`**: where to deploy the stack
- **`STATE_BUCKET`**: S3 bucket storing Terraform state (**must be globally unique**)
- **`LOCK_TABLE`**: DynamoDB table for Terraform state locking (prevents concurrent applies)
- **`REPO_NAME` / `IMAGE_TAG`**: ECR repo + tag for your image
- **`QDRANT_URL`**: your Qdrant **cluster endpoint** (REST API on port `6333`)
  - Use the **cluster endpoint** shown in Qdrant Cloud (load-balanced)
  - Avoid node-specific URLs like `node-0-...` (they may not expose the REST API correctly)
  - Example: `https://xyz-example.eu-central.aws.cloud.qdrant.io`
- **`QDRANT_API_KEY`**: optional; stored in Secrets Manager via `make aws-edge-secret`

Notes:

- `.env.local` is gitignored, and the repo `Makefile` auto-loads `.env` and `.env.local` when present.
- Do **not** store AWS access keys in `.env.local` (keep those in `~/.aws/credentials` via `aws configure` or use SSO).
- **Quick connectivity check (from your laptop)**:

```bash
curl -sS -H "api-key: $QDRANT_API_KEY" "${QDRANT_URL}:6333/collections" | head
```

### 2) Apple Silicon note (arm64 → x86_64)

This repo’s ECS/Fargate config defaults to **x86_64**. On Apple Silicon, set:

```bash
export DOCKER_DEFAULT_PLATFORM=linux/amd64
```

Or set it in `.env.local` (supported by `make aws-edge-push`).

### 3) Deploy

```bash
make doctor
make aws-edge-up
```

What it does:

- creates/updates Terraform backend (S3 + DynamoDB)
- builds and pushes the container image to ECR
- stores Qdrant API key in Secrets Manager (if provided)
- applies Terraform in `infra/aws/terraform`
- runs a one-shot ECS task to seed the KB

### 4) Get the public URL + verify

```bash
cd infra/aws/terraform
terraform output -raw gateway_public_url
curl -sS "$(terraform output -raw gateway_public_url)/health"
```

### Cleanup (stop charges)

```bash
cd infra/aws/terraform
terraform destroy
```

