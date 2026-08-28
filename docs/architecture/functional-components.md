# Hub Functional Component Registry

This is the human-readable inventory of Hub functional repository units (FRUs).
It is grounded in repository `main` at
`a6235b6c764f14fa9d97c262ba19ae1fc9df0264`, plus the documentation-only and
retired-runtime cleanup on branch `codex/frank-runtime-cleanup`. The cleanup does
not become `main` truth until merged.

Read [`capability-ontology.md`](capability-ontology.md) first. That document owns
the definitions of capabilities, domains, component kinds, relationships,
compositions, maturity, and claims. This file defines the concrete components.

## Registry rules

An FRU is included when another unit imports, calls, deploys, configures, operates,
or verifies it; when it owns a durable contract, state boundary, or security
boundary; or when it can be deployed, replaced, or deprecated independently.

Each record supplies:

- stable ID, component kind, and capability domains;
- bounded responsibility and source roots;
- interfaces, state authority, and dependencies;
- deployment composition, implementation status, lifecycle, and evidence;
- a positive claim and a nearby stronger claim that is not supported.

Implementation status, lifecycle, component evidence, and capability maturity are
independent. `E1` means code/contract exists, `E2` adds repository verification,
`E3` adds a deployment/operator path, and `E4` is accepted point-in-time operating
evidence. These component evidence labels do not automatically confer a `C0`–`C5`
capability maturity.

## Control, access, and shared contracts

| FRU · kind · domains | Responsibility and source | Interfaces, state, dependencies | Composition and status | Claim boundary |
|---|---|---|---|---|
| `FRU-EDGE-001` Gateway HTTP · `FC-SVC`/`FC-API` · `DOM-CTL` | HTTP control plane for runtime messages/events, search, tools, configuration, Review, Cases, Queue, HubFS, Hermes, processes, and Matrix transactions. `services/gateway_http/` | HTTP routes; clients Runtime/Queue/Cases/Eventbus/STT/Matrix; Review registry and mounted file roots | Local Compose; AWS baseline/edge · implemented, active · E2/E3 | Routes and profile wiring exist. No complete public-API stability or universal tenant-isolation claim |
| `FRU-AUTH-001` Review Auth · `FC-LIB`/`FC-STA` · `DOM-IDN`, `DOM-CTL` | Client, project, deployment, access-code policy, session, deploy-hook, capability, and repair-plan state. `services/gateway_http/review_auth.py` | Gateway admin/session routes; SQLite/Postgres abstraction; clients Postgres optional | Gateway local; AWS baseline RDS option · implemented, active · E2/E3 | Review-scoped authorization and sessions, not universal Hub identity or completed security assurance |
| `FRU-FS-001` HubFS and Review artifacts · `FC-API` · `DOM-CTL`, `DOM-DAT` | Allowlisted stat, content, directory, manifest, mirror, and artifact access. Gateway HubFS/Review routes | HTTP; configured filesystem roots; Review files and registered artifacts | Mounted Gateway profiles · implemented, active · E2/E3 | Bounded file access, not object-store durability, retention, malware scanning, or legal hold |
| `FRU-MODEL-001` Model profiles · `FC-CON` · `DOM-EXE`, `DOM-IDN` | Provider, endpoint, model, purpose, secret-handle, override, binding, and connectivity configuration. `infra/model-profiles.yaml`, resolver and Gateway admin routes | YAML contract; runtime resolution; audit JSONL; secret handles | Local and AWS bindings · implemented, active · E2/E3 | Validated binding structure, not proof that each provider/model is live, accurate, fast, or available |
| `FRU-COMMON-001` Common runtime library · `FC-LIB` · cross-domain | Configuration, errors, IDs, logging, model resolution, schemas, vault writes, and generated protocol bindings. `libs/common/` | Python imports; generated Agent/Queue bindings; no standalone state | Imported by current services · implemented, active · E2 | Internal primitives, not a stable customer SDK |
| `FRU-PROTO-001` Runtime and Queue protocols · `FC-CON` · `DOM-WRK`, `DOM-EXE` | Internal gRPC request, response, event, health, queue, claim, settlement, and inspection schemas. `proto/agent.proto`, `proto/queue.proto` | gRPC/protobuf; generated bindings consumed by Runtime, Sandbox, and Queue | Current service compositions · implemented, active · E2 | Current internal protocol, not a versioned public compatibility guarantee |

## Work orchestration, execution, and tools

| FRU · kind · domains | Responsibility and source | Interfaces, state, dependencies | Composition and status | Claim boundary |
|---|---|---|---|---|
| `FRU-QUEUE-001` Queue · `FC-SVC`/`FC-STA` · `DOM-WRK`, `DOM-DAT` | Durable enqueue/dequeue, lease, idempotency, retry, nack, dead-letter, inspection, and health. `inbox/`, `proto/queue.proto` | HTTP 8081; gRPC 50053; SQLite `messages`; single writer | Local Compose; AWS baseline on EFS · implemented, active · E2/E3 | Durable single-writer queue behavior, not replicated HA, zero-loss failover, or multi-region durability |
| `FRU-EVENT-001` Eventbus · `FC-SVC` · `DOM-WRK` | HTTP publish/subscribe runtime event distribution. `services/eventbus/` | HTTP 8082 `publish`, `subscribe`, `health`; memory/process lifetime | Local Compose; AWS baseline · implemented, active · E2/E3 | Event delivery while running, not a durable broker or replay log |
| `FRU-CASE-001` Cases · `FC-SVC`/`FC-STA` · `DOM-WRK`, `DOM-DAT` | Canonical cases, steps, slots, logs, audits, runs, spans, events, artifacts, streams, board state, and completion outputs. `services/cases/` | HTTP 8083; Cases SQLite tables; tool/Frank/Gateway consumers | Local Compose; AWS baseline on EFS · implemented, active · E2/E3 | Persisted workflow state, not replicated HA, automatic failover, or complete lifecycle controls |
| `FRU-FRANK-001` Frank native pipeline · `FC-WRK` · `DOM-WRK`, `DOM-AGT` | Queue-driven compilation, scheduling, recovery, execution, and reconciliation of native case pipelines. `services/frank/` | Queue/Cases/Eventbus/STT/Gateway HTTP; process contracts; case tools; model bindings | Local Compose; AWS baseline · implemented, active · E2/E3 | Only `native_case_pipeline` is supported. `direct` and `kanban` runtime values are explicitly rejected |
| `FRU-WORKER-001` Hermes worker queue · `FC-WRK` · `DOM-WRK`, `DOM-AGT` | Claims worker assignments and launches Hermes-compatible bounded execution. `services/hermes_worker_queue/` | Queue/Cases/Eventbus/STT/Gateway; Docker worker image target | Local Compose and build-only worker target · implemented, active · E2 | Current local worker behavior, not AWS parity, autoscaling, or a managed worker fleet |
| `FRU-PROCESS-001` Process contracts/compiler · `FC-CON`/`FC-PRC` · `DOM-WRK`, `DOM-AGT` | Parses Markdown process metadata, steps, edges, required slots, assignments, and dispatch behavior. `base/ops/processes/` and loader/compiler code | Markdown contract consumed by Frank/Cases/indexer; stored dispatch snapshots | Current native pipeline; local process indexer · implemented, active · E2 | Shipped process grammar and definitions, not a general planner or all future process types |
| `FRU-TOOLS-001` Registered tool suite · `FC-LIB`/`FC-TOL` · `DOM-EXE` | Case reads/writes, logs, status/runtime updates, completion, Review status, speech adapters, and example echo. `libs/tools/` | Tool manifests and Python contracts; Gateway/Cases/STT calls; timeout/network/memory policy | Runtime/Sandbox and Frank consumers · implemented, active · E2 | Registered tools within declared policy; network tools and side effects remain explicitly controlled |
| `FRU-RUNTIME-001` Runtime gRPC · `FC-SVC` · `DOM-EXE`, `DOM-CTL` | User-message submission, runtime event streaming, knowledge search, tool invocation, and health. `services/runtime_grpc/` | gRPC 50051; Agent protocol; Sandbox and Qdrant dependencies | Local Compose; AWS baseline/edge · implemented, active · E2/E3 | Current internal runtime surface, not full conversation persistence or a stable external SDK |
| `FRU-SANDBOX-001` Tool Sandbox · `FC-SVC` · `DOM-EXE` | Executes registered tools under timeout, memory, and network policy. `services/tool_sandbox/` | gRPC 50052 `RunTool`/`HealthCheck`; mounted registry; optional Gateway/STT calls | Local Compose; AWS baseline/edge · implemented, active · E2/E3 | Bounded execution controls, not hostile-code isolation equivalent to a hardened tenant VM |
| `FRU-STT-001` Speech-to-text · `FC-SVC`/`FC-ADP` · `DOM-EXE` | Health and transcription through local Whisper, OpenAI Whisper, or ElevenLabs-compatible paths. `services/stt_http/`, speech tools | HTTP 8765; guarded audio roots; provider APIs and secrets where selected | Local Compose; AWS baseline · implemented, active · E2/E3 | Provider abstraction and file-root controls, not guaranteed accuracy, latency, retention, or provider availability |
| `FRU-LLM-001` Internal model plane · `FC-SVC`/`FC-INF` · `DOM-EXE`, `DOM-DAT` | Loads a checksum-addressed model from S3/EFS and exposes internal OpenAI-compatible inference. AWS baseline Terraform and image/profile manifests | S3 artifact, EFS cache, HTTP inference, model-profile bindings | AWS baseline · implemented infrastructure path, active · E2/E3 | Artifact and binding path, not current endpoint health, model quality, throughput, or GPU capacity |

## Knowledge and retrieval

| FRU · kind · domains | Responsibility and source | Interfaces, state, dependencies | Composition and status | Claim boundary |
|---|---|---|---|---|
| `FRU-KBLIB-001` Knowledge library · `FC-LIB`/`FC-ADP` · `DOM-KNW` | Embedding interfaces, document/chunk models, and Qdrant store. `libs/kb/` | Python APIs; Qdrant HTTP/client dependency | Runtime and indexer consumers · implemented, active · E2 | Current retrieval abstraction, not provider portability beyond implemented adapters |
| `FRU-KB-001` Knowledge indexer/search plane · `FC-SVC`/`FC-STA` · `DOM-KNW`, `DOM-DAT` | Seeds and indexes knowledge for runtime vector search. `services/kb_indexer/`, Qdrant | Qdrant collection; Runtime search consumer; configured embedding/vector dimension | Local Compose; AWS consumes a configured endpoint but does not fully provision parity · implemented/partial · E2/E3 local | Local indexing/search, not complete production hosting, backup, restore, or AWS parity |
| `FRU-PROCINDEX-001` Process indexer · `FC-SVC` · `DOM-KNW`, `DOM-AGT` | Indexes process definitions for discovery. `services/process_indexer/` | Reads `base/ops/processes`; writes Qdrant | Local Compose one-shot · implemented, active · E2 | Local process retrieval integration, not AWS deployment parity |
| `FRU-VAULTAPI-001` Vault API · `FC-SVC`/`FC-API` · `DOM-KNW`, `DOM-DAT` | Lists vault content/contacts, initializes configured roots, and reports health. `services/vault_api/` | HTTP; configured vault root and auth; filesystem authority remains external/configured | Not wired into root Compose or AWS · partial, active · E2 | API implementation only; no primary-profile integration or complete authority/lifecycle contract |
| `FRU-VAULTIDX-001` Vault indexer · `FC-LIB`/`FC-ADP` · `DOM-KNW` | Scans and indexes vault files into a search target. `services/vault_indexer/` | Filesystem reads; Qdrant/search destination | Not wired into primary profiles · partial, active · E2 | Indexer code exists; tenancy, recovery, and supported composition are unspecified |

## Messaging and identity integrations

| FRU · kind · domains | Responsibility and source | Interfaces, state, dependencies | Composition and status | Claim boundary |
|---|---|---|---|---|
| `FRU-MATRIX-BRIDGE-001` Matrix bridge · `FC-SVC`/`FC-ADP` · `DOM-MSG` | Accepts Matrix transactions and bridges work/events. `services/matrix_bridge/` | HTTP 8084 transaction/health; Queue and Eventbus; Synapse appservice tokens | Local Compose · implemented, active · E2 | Adapter behavior, not active production registration or end-to-end delivery |
| `FRU-MATRIX-INGEST-001` Matrix ingest · `FC-SVC`/`FC-ADP` · `DOM-MSG` | Optional Sophia/appservice ingestion into Hub work and events. `services/ingest/` | Matrix Client/Appservice; Queue/Eventbus; gated room/user configuration | Local Matrix profile · implemented, gated/active · E2 | Optional ingest path, not enabled production operation |
| `FRU-FEEDS-001` Feeds · `FC-SVC`/`FC-ADP` · `DOM-MSG` | Fetches configured feed sources and emits events or work. `services/feeds/` | Feed configuration; Eventbus; optional Queue; local persisted feed data | Local Compose · implemented, active · E2 | Local feed processing, not complete source coverage or AWS parity |
| `FRU-MATRIX-LOCAL-001` Local Synapse · `FC-DEP` · `DOM-MSG`, `DOM-DEP` | Development Synapse/Postgres composition, rendered configuration, bots/appservices, and optional Sophia receiver. `infra/matrix/` | Matrix client/federation/appservice APIs; local Postgres/media/signing state | Separate local Compose · partial development profile · E3 | Development integration, not a supported full-product production composition |
| `FRU-MATRIX-ECS-001` Matrix on AWS baseline · `FC-INF`/`FC-DEP` · `DOM-MSG`, `DOM-DEP` | Synapse on ECS with RDS/EFS, DNS/TLS/federation, alarms, secrets, backup, and optional related services. `infra/aws_baseline_80/` | Matrix APIs; RDS, EFS, Secrets Manager, ALB/Route53/ACM, CloudWatch/SNS | AWS baseline · partial with historical E4 evidence | Substantial deployable profile; current release/live conformance and every DR credential rotation remain unproven |
| `FRU-MAS-001` Matrix Authentication Service · `FC-SVC`/`FC-INF` · `DOM-IDN`, `DOM-MSG` | Conditional MAS RDS/ECS/migration tasks, secrets, routes, alarms, and guarded MSC4108 migration. AWS baseline Terraform and runbook | OIDC/MSC4108; Synapse migration; Postgres and secrets | Conditional AWS baseline · partial/experimental · E3 | Implementation and rollout safeguards, not completed production authority cutover or QR-login operation |
| `FRU-HYPHA-001` Hypha admin broker · `FC-API`/`FC-ADP` · `DOM-MSG`, `DOM-IDN` | Typed, short-lived facade for bounded Synapse administration. `services/hypha_admin_broker/`, image/deploy/operations controls | HTTP session/snapshot/user/room/password/purge operations; memory-only sessions; Synapse Admin API | Separate AWS deployment path; not root Compose · implemented subsystem, experimental deployment · E2/E3 | Typed administration without client-held persistent admin credentials; publication/live deployment is not claimed |
| `FRU-MATRIX-EC2-001` Fresh standalone Matrix · `FC-INF`/`FC-DEP` · `DOM-MSG`, `DOM-DEP` | Dedicated VPC/EC2/EIP/Caddy/Synapse/Postgres/EBS with SSM-only access, secret injection, DLM, deploy, verify, and restore. `infra/matrix/aws/` | Matrix APIs; EC2/EBS/Postgres/Caddy; Secrets Manager/SSM/DLM | Standalone Matrix AWS profile · implemented deployable profile, active · E3 | Fresh deployment path exists; no claim a production instance is currently running |

## Agents, processes, and repository content

| FRU · kind · domains | Responsibility and source | Interfaces, state, dependencies | Composition and status | Claim boundary |
|---|---|---|---|---|
| `FRU-AGENT-FRANK-001` Frank agent package · `FC-AGT` · `DOM-AGT` | Frank persona, definition, configuration, memory/policy content, worker guidance, and review skills. `rolodex/agents/frank/` | Read by Frank/Hermes paths; model and process dependent | Native pipeline compositions · implemented, active · E2 | Shipped agent policy/content, not invariant behavioral quality across models and inputs |
| `FRU-AGENT-SOPHIA-001` Sophia agent package · `FC-AGT` · `DOM-AGT`, `DOM-MSG` | Communications-oriented persona and configuration. `rolodex/agents/sophia/` | Optional Matrix/ingest references; no Frank-equivalent worker boundary | Content/library use · implemented content, partial runtime role · E1 | Persona/configuration exists; no autonomous production-worker claim |
| `FRU-ROLODEX-001` Rolodex registry · `FC-CON` · `DOM-AGT`, `DOM-IDN` | Repository-owned agent identities, definitions, configuration, and people references. `rolodex/` | YAML/Markdown/JSONL content consumed by runtime and operators | Repository content · implemented, active · E2 | Registry structure, not dynamic multi-tenant identity lifecycle management |
| `FRU-REVIEW-SKILLS-001` Review skills · `FC-TOL`/content · `DOM-AGT` | Thirteen staged transcription, observation, screenshot, narrative, and synthesis skills. `base/ops/skills/` | Markdown skill contracts; consume review artifacts and model/tool context | Native Review process content · implemented, active · E1/E2 via process tests | Shipped instructions, not proof of correct output for every model/input |

## Deployment, operations, and assurance

| FRU · kind · domains | Responsibility and source | Interfaces, state, dependencies | Composition and status | Claim boundary |
|---|---|---|---|---|
| `FRU-COMPOSE-001` Local Compose topology · `FC-DEP` · `DOM-DEP` | Integrates core services with clients Postgres, Qdrant, persistent volumes, build-only worker, and debug profile. `docker-compose.yml` | Docker Compose service/network/volume contract | Local development · implemented, active · E3 | Broad development integration, not production HA or a supported on-premises product profile |
| `FRU-AWS-BASE-001` AWS baseline · `FC-INF`/`FC-DEP` · `DOM-DEP` | VPC, discovery, ECS services, ALB/TLS, RDS, EFS, logs, secrets, model preload, Matrix/MAS, backup, rollout foundations. `infra/aws_baseline_80/` | Terraform state/resource graph; manifests and rollout scripts | AWS full baseline · implemented, active · E3 | Explicit Terraform inventory only. The legacy path is not a price, service-count, capacity, or readiness promise |
| `FRU-AWS-EDGE-001` AWS edge · `FC-INF`/`FC-DEP` · `DOM-DEP` | CloudFront/WAF/ALB and Gateway/Runtime/Sandbox/KB-indexer topology. `infra/aws/terraform/` | Terraform state/resource graph and provider inputs | Narrow AWS edge profile · implemented, active · E3 | Smaller named profile, not parity with the full service graph |
| `FRU-ONPREM-001` On-premises Kubernetes prototype · `FC-INF`/`FC-DEP` · `DOM-DEP` | Kubernetes definitions for Gateway, Runtime, and Tool Sandbox. `infra/onprem/k8s/` | Kubernetes workload/service configuration | On-premises prototype · prototype, active · E1/E3 | Deployable three-component core only; Queue, Cases, Frank, data, Matrix, operations, and parity are absent |
| `FRU-PROFILES-001` Deployment profiles · `FC-CON` · `DOM-DEP`, `DOM-ASR` | Declares required services, state authorities, smoke commands, durability, and backup policy for named environments. `infra/deployment-profiles.yaml` | YAML consumed by static validator and docs | Cross-profile contract · implemented, active · E2 | Schema passes current checks; Matrix adoption text is stale and H-tier requirements are absent |
| `FRU-IMAGEENV-001` Image/environment manifest · `FC-CON`/`FC-EVD` · `DOM-DEP`, `DOM-ASR` | Maps ECS task images, commands, environment, and secret handles. `infra/image-env-manifest.yaml` | YAML checked against AWS Terraform | AWS baseline contract · implemented, active · E2 | Manifest/IaC consistency at a revision, not proof of current running task definitions |
| `FRU-EXTROOT-001` External-root contract · `FC-CON`/`FC-OPS` · `DOM-DEP`, `DOM-DAT` | Declares repository-external paths and validates resolution without committing private roots. `infra/external-roots.yaml`, scripts | YAML/environment/CLI; operator-owned filesystem | Local/operator compositions · implemented, active · E2 | Explicit handling, not availability, integrity, or backup of external data |
| `FRU-UPDATE-001` Operator update planner · `FC-OPS` · `DOM-DEP` | Compares target ref/profile with operator state, plans changes, and records apply only after smoke. `scripts/hub_update.py`, `deployments/operator-state.example.json` | CLI and operator-state JSON | Operator-managed nodes · implemented, active · E2/E3 | Controlled planning/apply boundary, not unattended fleet management or universal data rollback |
| `FRU-REVIEWPACKET-001` Review packet acceptance · `FC-OPS`/`FC-EVD` · `DOM-ASR`, `DOM-WRK` | Builds a case Review packet and runs bounded acceptance checks. `scripts/build_review_packet_for_case.py`, `scripts/run_review_packet_acceptance.py` | CLI, Cases/Gateway/file artifacts, test fixtures | Review workflow · implemented, active · E2 | Packet construction and checks, not all-customer acceptance or release conformance |
| `FRU-RELEASE-001` Build and rollout controls · `FC-OPS` · `DOM-DEP`, `DOM-ASR` | Static validation, immutable image publication, manual OIDC rollout, smoke, and source ledger. `.github/workflows/`, `scripts/prod_*`, operations docs | CI, registries, Terraform, smoke HTTP, operator approval | AWS profiles and Matrix subsystems · implemented, active · E2/E3 | Controls exist; no fully automated CD, isolated staging, or universal rollback claim |
| `FRU-TEST-001` Repository verification suite · `FC-EVD` · `DOM-ASR` | Tests services, contracts, auth, tools, workflows, profiles, Matrix controls, updates, and infrastructure invariants. `tests/`, static-check scripts | Pytest and CLI validators | Repository · implemented, active · E2 | Verification at the named commit, not live-environment conformance |
| `FRU-MATRIX-EVID-001` Matrix production evidence gate · `FC-EVD` · `DOM-ASR`, `DOM-MSG` | Records reviewed plan/apply, public smoke, load, metrics, alarms, and restore results. `docs/evidence/matrix-production/` and validator | Versioned JSON/text evidence tied to source `e62dfd9` | One historical AWS Matrix run · implemented evidence, active record · E4 | The recorded profile passed recorded checks; evidence does not transfer to current `main` or all Hub services |
| `FRU-FRESH-RECOVERY-001` Fresh Matrix recovery · `FC-OPS`/`FC-EVD` · `DOM-DEP`, `DOM-ASR` | Coordinates application-consistent snapshots and isolated restore rehearsals. Matrix DLM/restore scripts and docs | AWS DLM/EBS/Postgres controls; rehearsal CLI; evidence output | Fresh Matrix EC2 profile · implemented, active · E3 | Recovery mechanism exists; measured RPO/RTO requires an accepted run-specific artifact |

## Isolated, experimental, and lifecycle decisions

| FRU/candidate | Current evidence | Lifecycle and claim treatment |
|---|---|---|
| `FRU-HUBRUNTIME-001` isolated `hub-runtime` package · `FC-LIB`/`FC-WRK` | `repos/hub-runtime/` contains a separate package, CLI, OpenAI-compatible provider, Hermes loop, tool loader, and tests; no root service import or Compose/AWS wiring was found | Experimental/deprecation candidate · E2. Do not present as the primary runtime until an owner chooses integration or archive |
| Frank alternate runtimes | Current code accepts only `native_case_pipeline`; cleanup commit `847de04` removes obsolete runtime docs and Kanban fixtures | Deprecated. Old persisted values may remain readable but current producers do not schedule them |
| Superseded Frank/Sophia transition document | Described earlier layers and absent paths | Removed on cleanup branch at `847de04`; changelog retains provenance |
| AWS cost/service-count narrative | Historical documentation understated the current Terraform graph | Removed on cleanup branch at `26a99a0`; `infra/aws_baseline_80/` remains only as a compatibility-sensitive path |
| secS-magik, Dregg, Castalia integration claim | README text existed without runtime implementation; Matrix issue material rejects it for v0 | Unsupported/deprecation candidate. Do not claim as current capability |
| Older Matrix parity/adoption prose | Older EC2 and non-adopted assumptions conflict with newer profiles/evidence | Historical or stale-contract candidate; update additively before removing machine fields |
| `docs/plans/` | Dated design and work notes | Historical by default; never evidence of current implementation |
| `test-agent` | Exists under test/fixture-oriented rolodex content | Test fixture; excluded from product agent capability claims |

## State-authority catalog

| Data class and authority | Writers/readers | Durability boundary | Capability limit |
|---|---|---|---|
| Queue work · Queue SQLite `messages` | Queue is writer; Gateway, Frank, workers, Matrix, and operators use HTTP/gRPC | Local volume or AWS EFS file; one writer | Durable queue semantics, not replicated database HA |
| Workflow execution · Cases SQLite tables | Cases is writer; Frank, tools, Gateway, and worker read/write via HTTP | Local volume or AWS EFS file; one writer | Persistent audit/state, not multi-writer failover |
| Review identity/policy · Review Auth registry | Gateway Review Auth layer | SQLite or Postgres abstraction; AWS can use RDS | Review-scoped auth, not universal IAM |
| Knowledge vectors · Qdrant collection | KB/process/vault indexers write; Runtime reads | Local Compose is explicit; provider endpoint elsewhere | Retrieval, not full production backup/recovery |
| Hub files/artifacts · mounted filesystem paths | Gateway HubFS/Review routes and workflow producers | Local files or EFS by profile | Access contract, not general object lifecycle |
| Model artifacts · S3 object and EFS preload | Operator/build pipeline writes; llama plane reads | AWS baseline | Artifact integrity path, not inference availability |
| Matrix state · Synapse Postgres/media/signing/appservice material | Synapse and Matrix administration controls | Local Compose, ECS/RDS/EFS, or standalone EC2/EBS profile | Profile-specific messaging state, not Hub-wide identity |
| MAS identity state · MAS Postgres/secrets | MAS and guarded migration tasks | Conditional AWS resources | Infrastructure present; authority cutover unproven |
| Hypha sessions · process memory | Hypha broker only | Memory-only; Synapse remains identity authority | Short-lived facade, not identity system of record |
| Logs/evidence · structured logs, CloudWatch, versioned evidence files | Services, validators, operator controls | Profile-specific | Existence does not prove complete retention, alert response, or current release |

## Deployment-composition membership

| Component | Local Compose | AWS baseline | AWS edge | Matrix EC2 | On-premises K8s |
|---|---:|---:|---:|---:|---:|
| Gateway / Runtime / Sandbox | Yes | Yes | Yes | No | Yes |
| Queue / Cases / Eventbus | Yes | Yes | No | No | No |
| Frank / STT | Yes | Yes | No | No | No |
| Hermes worker queue | Yes | No named service | No | No | No |
| Internal llama model plane | No default service | Yes | No | No | No |
| KB indexer / Qdrant | Yes | Endpoint consumed; provisioning incomplete | KB indexer included | No | No |
| Process indexer | Yes | No | No | No | No |
| Matrix Synapse | Separate local profile | ECS/RDS/EFS resources | No | Dedicated profile | No |
| Matrix bridge / ingest / feeds | Yes, with ingest gating | Not complete profile parity | No | Requires external Hub integration | No |
| MAS / Hypha broker | No | Conditional/separate subsystems | No | Hypha may target Synapse | No |
| Vault API / indexer | Not wired | No | No | No | No |

Membership is explicit. “Cloud,” “self-hosted,” and “on-premises” are operating
contexts, not evidence that every component is present.

## Specification frontier: not claimable as delivered

These are functional specification units, not current capabilities.

| Proposed FRU · kind | Defined outcome | Missing implementation/evidence |
|---|---|---|
| `FRU-TIER-001` H1–H5 standard · `FC-SPC` | Machine-readable workload/deployment requirement tiers | Additive schema, per-tier component/data/security/recovery rules, provider overlays, validators, evidence |
| `FRU-ONPREM-PROD-001` production on-premises Hub · `FC-SPC` | Full or explicitly tier-scoped product composition on private infrastructure | Queue/Cases/Frank/data/Matrix/operations parity, install, upgrade, rollback, monitoring, recovery, hardware profiles |
| `FRU-STATE-HA-001` HA Queue/Cases · `FC-SPC` | Replicated production orchestration state | Managed/replicated design, migrations, failover/consistency tests, backup, restore, measured recovery |
| `FRU-OBJECT-001` durable object plane · `FC-SPC` | Authoritative versioned artifact/media storage | Object authority, retention, deletion/export, malware controls, backup/recovery |
| `FRU-OBS-001` observability/incident operations · `FC-SPC` | Metrics, traces, synthetics, dashboards, alarms, ownership, and runbooks | Cross-profile implementation, routing, exercises, retention, evidence |
| `FRU-STAGING-001` isolated staging · `FC-SPC` | Production-like release rehearsal and promotion | Isolated data/secrets/domain, representative load, migration rehearsal, rollback proof |
| `FRU-TENANT-001` Hub tenant identity · `FC-SPC` | Hub-wide users/staff/services, roles/policies, audit, lifecycle, and isolation | Identity contract, enforcement, offboarding, privileged access, isolation tests |
| `FRU-MAIL-001` Hub Mail · `FC-SPC` | Repository-defined mail capability | Merged code, contracts, state, security, provider boundary, deployment, operations, tests |
| `FRU-CLOUDAGENT-001` Matrix-only Hermes cloud agent · `FC-SPC` | Agent execution through a Matrix-only boundary | Merge/reconcile proposed work, deploy, test, and evidence |
| `FRU-REVIEWAUTH-FLOW-001` expanded Review policy flow · `FC-SPC` | Extended Review access-management workflow | Merge pending implementation, review compatibility, verify, and roll out |
| `FRU-DOCSTATUS-001` machine implementation ledger · `FC-SPC` | Generated component/capability implementation status | Merge/reconcile proposed docs, define consumers, validation, compatibility |
| `FRU-OUTBOX-001` Outbox · `FC-SPC` | Durable outbound delivery/retry boundary | `outbox/index.md` states unimplemented; needs schema, producers/consumers, retry/idempotency, tests, deployment |
| `FRU-PLANNER-001` Planner · `FC-SPC` | General planning/scheduling component | `planner/index.md` is specification content; needs runtime contract, state, integration, tests, deployment |

## Required upkeep

When a component changes, update its source, interfaces, state, dependencies,
composition membership, status, evidence, and claim boundary together. When a
shared contract changes, preserve older readers or provide a migration and
compatibility test. Historical documents must name their source revision and must
not be used as current evidence.

Future `infra/capabilities.yaml` or `infra/functional-units.yaml` files must be
additive and validated against all producers, consumers, persisted evidence, and
deployment-profile compatibility before they become authoritative.
