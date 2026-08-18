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
- Synapse uses native password authentication with public registration off;
  MAS, delegated authentication, and MSC4108 are not installed or configured;
- all three container defaults are immutable digest references;
- PostgreSQL and Synapse data live on an encrypted, delete-on-termination EBS
  block device. Bootstrap requires exactly one volume attached to the instance
  with the `hypha-fresh-synapse-data` tag, resolves that exact volume's Nitro
  by-id device, formats a blank device with the deployment-specific
  `hypha-matrix-data` XFS label, accepts only that exact label on a retry, and
  persists one UUID mount entry;
- Terraform creates only the Secrets Manager container. Secret values are
  fetched on the instance at runtime and never enter Terraform variables,
  state, user data, or bootstrap logs.

### Two-stage deployment

1. Run the one-time root bootstrap with the exact guarded profile and region:
   `python3 scripts/bootstrap_fresh_synapse_account.py --profile
   zenith-hypha-free --region us-east-1`. This creates only the retained,
   private, encrypted, versioned state bucket and the bounded
   `HyphaSynapseDeploymentRole`. The prompt also wires a `$30` monthly AWS
   Budget to the primary account email without echoing or committing it, plus
   SNS-backed Free Plan expiry alerts at 60, 30, 14, and 7 days. Confirm the
   SNS subscription email, then require
   `python3 scripts/verify_fresh_synapse_alerts.py --profile
   zenith-hypha-synapse --region us-east-1` to pass before runtime activation.
   The verifier checks the budget, confirmed subscription, and four schedules
   without printing the email endpoint. Configure the local
   `zenith-hypha-synapse` profile to assume that role from
   `zenith-hypha-free`; all subsequent commands use the assumed role.
2. Initialize the isolated backend with `terraform init -backend-config=
   backend.hcl` after copying `backend.hcl.example` to an ignored
   `backend.hcl`.
3. Copy `terraform.tfvars.example` to an ignored `terraform.tfvars`. Replace the
   example hostname and AMI identifier, leaving `enable_runtime = false`.
4. Run `terraform plan` and `terraform apply`. This creates
   the network, IAM role, and an empty Secrets Manager secret, but no EC2
   runtime.
5. Run `python3 scripts/populate_fresh_synapse_secret.py --profile
   zenith-hypha-synapse --region us-east-1`. The script requires the assumed
   deployment role, generates all four values locally, writes them through a
   mode-0600 temporary file, creates `AWSCURRENT`, deletes the temporary file,
   and prints only safe version metadata.
6. Set `enable_runtime = true`, then plan and apply again. Bootstrap waits
   for the `AWSCURRENT` version, fetches it directly from Secrets Manager, and
   validates its exact key set before Docker starts. The provider's
   `aws_secretsmanager_secret_version` declaration is intentionally disabled:
   enabling that data source would copy `SecretString` into Terraform state.
7. Point the hostname A record at `terraform output -raw elastic_ip`. Caddy
   obtains and renews TLS automatically; the public endpoint is the HTTPS-only
   `matrix_url` output.

The EC2 role has the AWS-managed `AmazonSSMManagedInstanceCore` policy. Its
inline policy permits `secretsmanager:GetSecretValue` only on the exact
module-created secret ARN and read-only `ec2:DescribeVolumes` solely to bind
bootstrap to its tagged attached data volume. It has no wildcard secret access.

### Administration

There is no SSH configuration. After public TLS is valid, create the sole
native-password administrator through the exact assumed-role wrapper:

```bash
python3 scripts/provision_fresh_synapse_admin.py \
  --profile zenith-hypha-synapse --region us-east-1
```

The wrapper permits only `@beaver:synapse.zenith-research.ca`, reads only the
registration authority from the exact runtime secret in memory, creates the
account with Synapse's shared-secret native registration API and `admin=true`,
and stores the generated password in macOS Keychain without printing it. Use
the `instance_id` output for subsequent Session Manager administration.

### Destruction and persistence

This module is for a new disposable homeserver. The root and Matrix data EBS
volumes are encrypted and deleted with the instance. Export or back up anything
that must survive before destroying the stack. The Secrets Manager secret uses
a seven-day recovery window.
