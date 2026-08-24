# Hub Matrix Server

Matrix (Synapse) deployment for hub-to-hub communication across the Zenith network.

Two deployment paths — local Docker and AWS EC2. See `DEPLOYMENT_PARITY.md` for the source-of-truth, backup/restore, appservice, and cloud/self-hosted parity contract.

---

## Local (Docker Compose)

**Requirements:** Docker + Docker Compose, existing `agentnet` network

```bash
# 1. Create agentnet if not already running
docker network create agentnet

# 2. Configure environment
cp infra/matrix/config/.env.example .env
# Edit .env — set MATRIX_SERVER_NAME and generate secrets:
#   openssl rand -hex 32   (run 3 times for the three secrets)

# 3. Start Matrix
docker compose -f infra/matrix/docker-compose.yml up -d

# 4. Create first admin account
docker exec -it matrix-synapse \
  register_new_matrix_user -c /data/homeserver.yaml http://localhost:8008 \
  -u admin -p YOUR_PASSWORD -a

# 5. Verify
curl http://localhost:8008/health
```

**Volumes:**
- `matrix-db-data` — Postgres data
- `matrix-data` — Synapse data, media store, signing key

---

## AWS: fresh standalone Synapse

`infra/matrix/aws` is intentionally a fresh, single-instance deployment for AWS
account `610992396917` in `us-east-1`. It does not import or refer to any old
Matrix state. It creates a dedicated VPC, public subnet, Elastic IP, Caddy TLS
edge, Synapse, and PostgreSQL on one EC2 instance.

Security boundaries:

- the AWS provider rejects every account except `610992396917` and the region
  variable rejects every region except `us-east-1`;
- the operator must supply a reviewed, explicit Amazon Linux 2023 `ami_id`;
- only TCP 80 and 443 are public, and only Caddy publishes host ports;
- administration uses Session Manager through the instance role; there is no
  SSH key or port 22 path;
- a CloudFormation-owned permissions boundary caps the instance role at SSM
  core, exact runtime-secret read, and attached-volume discovery even if an
  inline role policy is changed; routine deployment cannot terminate or stop
  EC2 instances, and security-group changes/reboots require the exact Synapse
  resource tags;
- Synapse uses native password authentication with public registration off;
  MAS, delegated authentication, and MSC4108 are not installed or configured;
- all four container inputs are immutable digest references;
- PostgreSQL and Synapse data live on an encrypted, delete-on-termination EBS
  block device. Bootstrap requires exactly one volume attached to the instance
  with the `hypha-fresh-synapse-data` tag, resolves that exact volume's Nitro
  by-id device, formats a blank device with the deployment-specific
  `hypha-matrix` XFS label, accepts only that exact label on a retry, and
  persists one required UUID mount entry;
- Docker has a persistent `RequiresMountsFor=/opt/matrix-data` dependency, so
  PostgreSQL, Synapse, and Caddy cannot start against empty root-filesystem
  directories after a data-volume mount failure;
- Terraform creates only the Secrets Manager container. Secret values are
  fetched on the instance at runtime and never enter Terraform variables,
  state, user data, or bootstrap logs.
- the root bootstrap owns hourly and daily application-consistent multi-volume
  DLM snapshots. PostgreSQL checkpoint/XFS freeze and successful pre/post tags
  are mandatory, and broker rollout additionally requires a recent isolated
  restore. See `docs/operations/fresh-synapse-backup-restore.md`.

### One-command deployment

Requirements are Python 3, AWS CLI, Terraform, and a configured
`zenith-hypha-free` profile for the target account. Supply the reviewed AMI,
public hostname, and exact broker image digest:

```bash
python3 scripts/deploy_fresh_synapse.py \
  --profile zenith-hypha-free \
  --region us-east-1 \
  --hostname synapse.zenith-research.ca \
  --ami-id ami-0332d564d76dbd8d6 \
  --admin-broker-image 610992396917.dkr.ecr.us-east-1.amazonaws.com/zenith-hub-prod-runtime-grpc@sha256:<reviewed-64-hex-digest>
```

On the first run, the launcher invokes the guarded bootstrap and privately
prompts for the budget/expiry-alert email. CloudFormation creates the retained
state bucket, the credential-only `HyphaSynapseTerraformSource` user, and the
exact-trust `HyphaSynapseDeploymentRole`. The bootstrap creates at most one
access key, writes it directly to the mode-0600 local AWS credentials file,
configures the source and assumed-role profiles, and verifies both identities.
No manual IAM user, access-key, profile, backend, or variable-file setup is
required. The first launch also prompts twice without echo for the dedicated
Hypha administration secret and stores only its scrypt verifier. Once those
exact profiles are installed, routine launcher reruns do
not invoke root bootstrap; run the bootstrap script directly only to create or
repair that authority chain.

The launcher then initializes isolated state, validates each saved Terraform
plan against exact resource/action allowlists, creates the base resources,
populates the runtime secret directly into Secrets Manager, and launches one
EC2 instance plus one Elastic IP. It never reads `SecretString` into Terraform
state. On-host bootstrap provisions or verifies only the hidden
`_hypha_admin_broker` service administrator. Reruns are idempotent and
complete partial base applies before runtime activation. Before reporting
success it requires SSM/cloud-init readiness, the exact XFS mount, Docker,
healthy PostgreSQL and Synapse containers, and an internal Matrix HTTP 200.
Its final JSON contains only the instance identifier, public URL, and exact DNS
A record.

User data is first-boot input. Because the persistent data EBS block is inline
and replacement would delete it, Terraform ignores later user-data diffs on an
existing host. Reconcile boot-policy changes through SSM, verify the live files,
and repeat controlled-reboot acceptance; fresh instances receive the current
template directly.

The bootstrap also creates a `$30` monthly budget and SNS-backed Free Plan
expiry alerts at 60, 30, 14, and 7 days. Email confirmation is recommended but
does not block the runtime. Verify it without disclosing the endpoint:

```bash
python3 scripts/verify_fresh_synapse_alerts.py \
  --profile zenith-hypha-synapse --region us-east-1
```

After setting the emitted A record, Caddy obtains and renews TLS automatically;
the public endpoint is the HTTPS-only `matrix_url` output.

The EC2 role has the AWS-managed `AmazonSSMManagedInstanceCore` policy. Its
inline policy permits `secretsmanager:GetSecretValue` only on the exact
module-created secret ARN and read-only `ec2:DescribeVolumes` solely to bind
bootstrap to its tagged attached data volume. It has no wildcard secret access.

### Administration

There is no SSH configuration and the launcher creates no Matrix users or
administrators. Account provisioning is a separate, explicit native-Synapse
operation after public TLS is valid. Use the `instance_id` output for Session
Manager administration.

### Destruction and persistence

The root and Matrix data EBS volumes are encrypted and deleted with the
instance. The root bootstrap separately retains application-consistent hourly
and daily DLM snapshot sets, and the deployment gate requires current backup
and isolated-restore evidence. Review that evidence and the recovery runbook
before destroying the runtime; delete-on-termination is still destructive to
the live volumes. The Secrets Manager secret uses a seven-day recovery window.
