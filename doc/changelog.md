0001
 <empty scaffold> --> <agent platform runtime scaffold>
Implemented infrastructure-grade backend plumbing: FastAPI gateway (HTTP + SSE), async gRPC runtime, Qdrant-backed KB vector search interface, tool registry + strict manifests/schemas, and subprocess-based tool sandbox; all runnable locally via Docker Compose.
Provides a generalized, contract-first runtime substrate without any agent reasoning logic, enabling end-to-end flow validation (message ingest → events → KB search → tool execution) with secure defaults (validation, timeouts, least privilege, structured logs).

0002
 <local-only compose> --> <AWS + on-prem deployment baselines>
Added AWS Terraform baseline for ECS/Fargate (ALB + WAF, optional CloudFront) with Cloud Map service discovery and strict security groups; added on-prem Kubernetes YAML example + guides for both environments.
Enables reproducible deployment patterns for many external chat users, including WebSocket/SSE stability guidance (idle timeouts, heartbeats, scaling) while keeping the application code unchanged (config-only environment switching).

0003
 <aws deployment baseline> --> <aws_baseline_80 terraform variant>
Added a cost-oriented AWS Terraform variant under `infra/aws_baseline_80/` targeting an always-on ~$80/month footprint (3× small Fargate services + 1× ALB + 1× NAT) with Cloud Map discovery, least-privilege IAM, Secrets Manager for Qdrant API key, and strict security group rules.
Provides a minimal, reproducible production baseline for ~100 DAU that keeps `runtime-grpc` and `tool-sandbox` private while exposing only `gateway-http`, with a deployment guide covering state backend, ECR image pushes, verification, rollback, and cost drivers.

0004
 <multi-doc onboarding> --> <single entrypoint + aws edge automation>
Added `infra/README.md` as the single “start here” entry point, updated the root `README.md` with a top-level Quickstart, and made the AWS edge path default to CloudFront+WAF+ALB with no ALB TLS requirement (`enable_https=false`, `enable_cloudfront=true` in examples).
Reduces newcomer setup friction by providing a minimal, repeatable AWS spin-up flow via Make targets + small scripts (doctor checks, Terraform state backend bootstrap, ECR build/push, optional Qdrant secret set, and KB seed) while keeping all application code unchanged.

0005
 <nested agent-platform directory> --> <repo-root agent platform layout>
Flattened the repository by moving all runtime/infra/docs from `agent-platform/` into the repo root; removed the leftover local `.venv-proto`; and updated the root Makefile/gitignore to load `.env`/`.env.local` and ignore `.env.local`.
Eliminates the extra nesting so local + AWS workflows run consistently from the repository root, while keeping infra/resource naming intact.

0006
 <ad-hoc setup via chat> --> <documented onboarding + env template>
Added `doc/setup.md` and `.env.local.example`, and linked them from the root and infra READMEs; also improved the `Makefile` so `AWS_PROFILE` (and optional `DOCKER_DEFAULT_PLATFORM`) can be driven from `.env.local`.
Gives new users a repeatable, low-friction path to configure GitHub SSH (multi-account) and deploy the AWS edge stack without relying on tribal knowledge.

0007
 <terraform init failure> --> <valid WAFv2 HCL blocks>
Fixed `infra/aws/terraform/waf.tf` to use multi-line nested blocks for `override_action` (required by HCL), resolving Terraform initialization errors during `make aws-edge-apply`.
Unblocks AWS edge deployments without changing any WAF behavior.

0008
 <AWS SG rule apply failure> --> <valid security group rule descriptions>
Updated `infra/aws/terraform/security_groups.tf` rule descriptions to only use characters accepted by EC2 security group rule APIs (removing `->` / punctuation that AWS rejects).
Prevents `AuthorizeSecurityGroupIngress/Egress` 400 errors so `terraform apply` can complete.

0009
 <baseline80 SG rule risk> --> <baseline80 description hardening>
Updated `infra/aws_baseline_80/security_groups.tf` ingress/egress descriptions to avoid characters rejected by EC2 security group rule APIs.
Ensures the cost-focused baseline variant can be applied without the same description-related failures.

0010
 <aws-edge-seed python quoting bug> --> <valid subnet join for ecs run-task>
Fixed `Makefile` `aws-edge-seed` to join `private_subnet_ids` from Terraform output without invalid escaping, preventing a Python `SyntaxError` and allowing the KB seeding task to run.
Unblocks the final `make aws-edge-up` step (`aws-edge-seed`) after infra successfully applies.

0011
 <kb-indexer qdrant wait without auth> --> <authenticated readiness check for Qdrant Cloud>
Updated `services/kb_indexer/main.py` so `wait_for_qdrant` passes `QDRANT_API_KEY` to `QdrantClient` when polling `get_collections()`.
Prevents false “not ready” failures against secured Qdrant endpoints (which can respond with 404/401 until authenticated), allowing the seed task to complete.

0012
 <qdrant endpoint confusion> --> <documented cluster endpoint requirement>
Updated `doc/setup.md` and `.env.local.example` to clarify that `QDRANT_URL` must be the Qdrant Cloud **cluster endpoint** (load-balanced), and to avoid node-specific URLs like `node-0-...`.
Reduces setup friction and prevents seeding/runtime failures caused by pointing at the wrong Qdrant hostname.
