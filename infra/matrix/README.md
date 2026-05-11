# Hub Matrix Server

Matrix (Synapse) deployment for hub-to-hub communication across the Zenith network.

Two deployment paths — local Docker and AWS EC2.

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

## AWS (EC2 + Terraform)

Synapse is stateful (Postgres + media store on EBS). EC2 is simpler than Fargate for this workload.

**What gets created:**
- EC2 instance (default: `t3.small`, Amazon Linux 2023)
- Encrypted EBS data volume (default: 30 GB)
- Elastic IP (stable DNS target)
- Security group (ports 8008, 8448, optional 22)
- Optional: VPC + subnet + IGW (if no existing VPC provided)

**DNS setup after deploy:**
```
A record:   matrix.yourdomain.com → <elastic_ip output>
SRV record: _matrix._tcp.yourdomain.com → matrix.yourdomain.com:8448  (federation)
```

**Deploy:**

```bash
cd infra/matrix/aws
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — set server name, secrets, region

terraform init
terraform plan
terraform apply

# After apply — get the IP
terraform output elastic_ip
```

**First admin account (SSH into instance):**
```bash
docker exec -it matrix-synapse \
  register_new_matrix_user -c /data/homeserver.yaml http://localhost:8008 \
  -u admin -p YOUR_PASSWORD -a
```

**Secrets management:** In production, store secrets in AWS Secrets Manager and inject via user data or SSM Parameter Store. The current setup injects from Terraform variables — keep `terraform.tfvars` out of version control.

---

## Architecture notes

- Federation enabled by default — enables hub-to-hub messaging across the Zenith network
- Public registration disabled by default — accounts created via admin API only
- Local: joins existing `agentnet` bridge so hub services reach Matrix at `matrix-synapse:8008`
- AWS: separate bridge network; hub services reach via Elastic IP or DNS

## Upgrading Synapse

```bash
# Local
docker compose -f infra/matrix/docker-compose.yml pull && \
  docker compose -f infra/matrix/docker-compose.yml up -d

# AWS
ssh ec2-user@<elastic_ip>
docker compose -f /opt/docker-compose.matrix.yml pull && \
  docker compose -f /opt/docker-compose.matrix.yml up -d
```

## Backup

**Local:** `docker run --rm -v matrix-db-data:/var/lib/postgresql/data -v $(pwd):/backup alpine tar czf /backup/matrix-db-$(date +%Y%m%d).tar.gz /var/lib/postgresql/data`

**AWS:** Snapshot the EBS volume — `aws ec2 create-snapshot --volume-id <ebs_volume_id_output> --description "matrix-backup-$(date +%Y%m%d)"`
