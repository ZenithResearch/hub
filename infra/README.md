# Infrastructure — Start Here

This repo supports multiple deployment “shapes” without application code changes (configuration only via environment variables and secrets).

New to the repo? Follow [`doc/setup.md`](../doc/setup.md) first for GitHub SSH + AWS prerequisites and the `.env.local` workflow.

## Choose a path

| Path | Best for | Public edge | Notes |
| --- | --- | --- | --- |
| Local (Compose) | Dev/demos | `gateway-http` on localhost | Fastest; includes local Qdrant container |
| AWS Edge | Many external users | CloudFront + WAF + ALB | More moving parts; best external posture |
| AWS Baseline (~$80/mo) | Small always-on footprint | ALB only | Cost-focused; easiest AWS runtime |
| On‑prem | Self-hosted | Your ingress/WAF | Kubernetes example included |

### Local (fastest)

- Use when: development, demos, or validating plumbing quickly.
- Docs: see the local quickstart in [`README.md`](../README.md).

Minimum commands:

```bash
cp .env.example .env
make up
```

### AWS Edge (CloudFront + WAF + ALB → gateway)

- Use when: many external users, global edge caching/acceleration, DDoS/WAF at the edge.
- Public exposure: **only** `gateway-http` (via CloudFront/ALB).
- Private services: `runtime-grpc`, `tool-sandbox` (internal-only, via service discovery).
- Docs: [`infra/aws/README.md`](aws/README.md)
- Terraform: [`infra/aws/terraform/`](aws/terraform/)

### AWS Baseline (~$80/mo)

- Use when: smallest always-on AWS footprint for ~100 DAU.
- Public exposure: **only** `gateway-http` (ALB HTTP).
- Private services: `runtime-grpc`, `tool-sandbox` (internal-only, via Cloud Map).
- Docs: [`infra/aws_baseline_80/DEPLOYMENT.md`](aws_baseline_80/DEPLOYMENT.md)
- Terraform: [`infra/aws_baseline_80/`](aws_baseline_80/)

### On‑prem / self‑hosted

- Use when: Kubernetes clusters, private datacenters, or “air‑gapped-ish” environments.
- Docs: [`infra/onprem/README.md`](onprem/README.md)
- Example manifests: [`infra/onprem/k8s/`](onprem/k8s/)

