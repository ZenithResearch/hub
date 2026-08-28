# Infrastructure — Start Here

This repo contains multiple current deployment shapes. The approved target for all
of them is a virtually private Hub whose only external admission path is
secS-magik. Existing public Gateway and Matrix routes predate that contract and are
non-conformant migration surfaces, not target architecture. See
[`../docs/architecture/private-exposure-boundary.md`](../docs/architecture/private-exposure-boundary.md).

Canonical deployment profile contract:

- [`DEPLOYMENT_PROFILES.md`](DEPLOYMENT_PROFILES.md)
- [`deployment-profiles.yaml`](deployment-profiles.yaml)

External local operational substrate contract:

- [`EXTERNAL_ROOTS.md`](EXTERNAL_ROOTS.md)
- [`external-roots.yaml`](external-roots.yaml)

Agent model/profile configuration contract:

- [`MODEL_PROFILES.md`](MODEL_PROFILES.md)
- [`model-profiles.yaml`](model-profiles.yaml)

New to the repo? Follow [`doc/setup.md`](../doc/setup.md) first for GitHub SSH + AWS prerequisites and the `.env.local` workflow.

## Choose a path

| Path | Best for | Current edge | Target status |
| --- | --- | --- | --- |
| Local (Compose) | Dev/demos | `gateway-http` on localhost | Must become loopback/private-network plus embedded or co-deployed secS admission |
| AWS Edge | Current external web path | CloudFront + WAF + ALB to Gateway | Non-conformant until edge admits only secS operations and Hub services are private |
| AWS production baseline | Current codified Hub topology | ALB plus profile-specific Matrix edge | Deployable substrate; public paths do not prove the target |
| On‑prem | Self-hosted prototype | Operator ingress/WAF | Prototype must replace direct Gateway ingress with embedded or co-deployed secS-only admission |

### Local (fastest)

- Use when: development, demos, or validating plumbing quickly.
- Docs: see the local quickstart in [`README.md`](../README.md).

Minimum commands:

```bash
cp .env.example .env
make up
```

### AWS Edge (current CloudFront + WAF + ALB → Gateway)

- Use when: many external users, global edge caching/acceleration, DDoS/WAF at the edge.
- Current public exposure: `gateway-http` through CloudFront/ALB. This is a
  documented legacy path, not the target security boundary.
- Private services: `runtime-grpc`, `tool-sandbox` (internal-only, via service discovery).
- Docs: [`infra/aws/README.md`](aws/README.md)
- Terraform: [`infra/aws/terraform/`](aws/terraform/)

### AWS production baseline

- Use when: operating or reviewing the current AWS ECS production topology.
- Current public exposure: Gateway through the ALB, plus profile-specific Matrix
  routes. Both must move behind secS-owned admission for target conformance.
- Private services: runtime, sandbox, queue, Cases, Eventbus, STT, Frank, and the internal model plane through service discovery and scoped security groups.
- Docs: [`infra/aws_baseline_80/DEPLOYMENT.md`](aws_baseline_80/DEPLOYMENT.md)
- Terraform: [`infra/aws_baseline_80/`](aws_baseline_80/)

The `aws_baseline_80` directory name is retained for Terraform path, state, and
operator compatibility. It is not a current monthly-price, service-count, user-
capacity, or production-readiness claim.

### On‑prem / self‑hosted

- Use when: Kubernetes clusters, private datacenters, or “air‑gapped-ish” environments.
- Docs: [`infra/onprem/README.md`](onprem/README.md)
- Example manifests: [`infra/onprem/k8s/`](onprem/k8s/)
