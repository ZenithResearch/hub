# Hub Capability Claims

This catalogue contains the complete top-level Hub claim surface. It is intentionally
limited to seven outcomes: DevGraph, Matrix, Queue, inference, object storage,
private exposure, and operability. The many services, workers, tools, policies, and
data stores below these outcomes are functional components, not separate product
claims.

The target architecture and current gaps are defined in
[`private-exposure-boundary.md`](private-exposure-boundary.md). Claim vocabulary
is defined in [`capability-ontology.md`](capability-ontology.md), and component
identities remain in [`functional-components.md`](functional-components.md).

Unless stated otherwise, review date is `2026-08-27` and owner is **unassigned**.
Hub evidence is assessed on branch `codex/frank-runtime-cleanup`; DevGraph evidence
is assessed at `1b11116`; secS-magik evidence is assessed at `8cf8e0a`. Current
implementation evidence describes substrates only. Every target claim is C0 until
secS admits its operations and dispatches a verified context to a private Hub
handler.

`SEC-SECS-001` is a required security substrate, not a required remote service. Hub
may import a pinned secS package into its receiver or include a pinned secS unit in
the deployment. Both modes must preserve the same fail-closed admission contract.

## Graph

### `CAP-GRAPH-001` — Provide DevGraph

- **Domains / actors / outcome:** `DOM-GPH`; admitted users, agents, and internal services can read or mutate repository and work-graph facts through policy-filtered graph operations.
- **Inputs → outputs / side effects:** secS-verified semantic graph operations and typed graph payloads → nodes, edges, queries, change events, or bounded errors; mutations write DevGraph authorities and outbox records.
- **Components / interfaces:** future `FRU-DEVGRAPH-ADAPTER-001`, DevGraph API/client/storage/auth/redaction/outbox components, and future `FRU-SECS-ADAPTER-001`; secS operation → verified context → private DevGraph handler.
- **State / external dependencies:** DevGraph's declared graph store and outbox own graph truth and publication state; `SEC-SECS-001` owns final admission whether embedded or co-deployed. Neo4j or other configured storage remains a DevGraph dependency.
- **Compositions / maturity:** target private local, cloud, and on-premises compositions are C0. The separate DevGraph repository contains a repository-verified internal substrate, but no Hub/secS integration or deployment evidence.
- **Constraints / negative claims:** no direct public DevGraph API, no claim that DevGraph is currently part of Hub, no production deployment claim, and no implication that derived indexes replace graph authority.
- **Evidence:** DevGraph README, code, and tests at `1b11116`; Hub repository search showing no DevGraph adapter; private-boundary acceptance criteria.

## Messaging

### `CAP-MATRIX-001` — Provide Matrix

- **Domains / actors / outcome:** `DOM-MSG`; admitted users, agents, appservices, and operators can exchange supported Matrix messages and perform bounded administration without addressing Synapse directly.
- **Inputs → outputs / side effects:** secS-verified semantic Matrix operations and typed room/event/admin payloads → Matrix results, Hub work/events, or bounded errors; may mutate Synapse and enqueue Hub work.
- **Components / interfaces:** future `FRU-SECS-ADAPTER-001`, private Matrix proxy/facade, current bridge/ingest/Hypha components, and Synapse; secS operation → verified context → private Matrix operation → Synapse.
- **State / external dependencies:** Synapse Postgres/media/signing/appservice material owns Matrix truth; Queue remains a separate work authority; `SEC-SECS-001` owns final external admission whether embedded or co-deployed.
- **Compositions / maturity:** target private compositions are C0. Existing local, ECS, and EC2 Matrix substrates have C3/C4 implementation evidence and historical C5 evidence for one revision, but their direct exposure bypasses secS.
- **Constraints / negative claims:** Synapse client, federation, admin, bridge, and ingest endpoints may not be directly public in the target. Historical evidence does not establish current target conformance or universal federation/client support.
- **Evidence:** current Matrix services, IaC, tests, runbooks, and `docs/evidence/matrix-production/`; repository exposure audit; private-boundary invariants.

## Work

### `CAP-QUEUE-001` — Provide durable Queue

- **Domains / actors / outcome:** `DOM-WRK`; admitted producers, workers, agents, and operators can persist, claim, settle, retry, inspect, and dead-letter asynchronous work.
- **Inputs → outputs / side effects:** secS-verified semantic Queue operations, queue/message identifiers, payloads, idempotency keys, leases, and settlement results → messages and status; writes attempts, leases, errors, and timestamps.
- **Components / interfaces:** future `FRU-SECS-ADAPTER-001`, `FRU-QUEUE-001`, `FRU-PROTO-001`, plus internal workflow consumers such as Cases, Frank, and workers; no raw external Queue HTTP/gRPC endpoint.
- **State / external dependencies:** Queue SQLite currently owns work truth and requires one active writer; its volume/EFS supplies storage. `SEC-SECS-001` owns final external admission whether embedded or co-deployed.
- **Compositions / maturity:** target private compositions are C0. Current local and AWS single-writer Queue substrates are integrated/deployable at C3/C4, but no secS operation manifest or adapter exists.
- **Constraints / negative claims:** no replicated HA, multi-region durability, zero-loss failover, horizontal Queue scaling, or direct external Queue access claim. Internal trusted service-to-service flows require an explicit policy boundary.
- **Evidence:** `inbox/`, Queue protocol and tests, Compose/AWS definitions, deployment-profile validator, and repository audit showing no secS integration.

## Inference

### `CAP-INFER-001` — Provide private inference

- **Domains / actors / outcome:** `DOM-INF`; admitted agents and services can run configured model inference inside the private Hub boundary and receive a structured result or bounded failure.
- **Inputs → outputs / side effects:** secS-verified semantic inference operation, model binding, prompt/input, limits, and correlation context → model output, usage/diagnostic metadata, or error; may read model artifacts and write operational evidence.
- **Components / interfaces:** future `FRU-SECS-ADAPTER-001`, private inference server, model-profile resolver, Runtime/Sandbox and provider adapters where selected; secS operation → verified context → private inference handler.
- **State / external dependencies:** model artifacts and configuration are authorities for loaded models/bindings; object storage may supply artifacts. External inference providers, if configured, are explicit dependencies rather than Hub-owned compute.
- **Compositions / maturity:** target private compositions are C0. Existing llama-server and model-preload profile paths provide a C4 deployable substrate in named profiles, without secS-gated target evidence.
- **Constraints / negative claims:** no direct public inference endpoint; model quality, accuracy, latency, capacity, cost, and third-party provider availability are not guaranteed. Sandboxing does not establish hostile-tenant isolation.
- **Evidence:** model profile contracts and tests, inference/model IaC and image manifests, local/AWS configuration, and secS-integration gap audit.

## Objects

### `CAP-OBJECT-001` — Provide object storage

- **Domains / actors / outcome:** `DOM-OBJ`; admitted users, agents, and services can put, get, list, version, retain, and delete typed objects through one policy-enforced authority.
- **Inputs → outputs / side effects:** secS-verified semantic object operations, object key/namespace, bytes or stream, metadata, integrity and retention policy → object/version metadata or bytes; mutates authoritative object and audit state.
- **Components / interfaces:** future `FRU-SECS-ADAPTER-001`, future `FRU-OBJECT-001`, provider adapter, integrity scanner, lifecycle/recovery controls; HubFS and existing file mounts are migration inputs, not the target authority.
- **State / external dependencies:** configured S3-compatible or on-premises object service owns object bytes and versions; metadata/audit authority must be explicit. `SEC-SECS-001` owns final admission whether embedded or co-deployed.
- **Compositions / maturity:** all target compositions are C0. Existing S3 model-artifact/Terraform-state uses and EFS/local artifact files are narrower storage uses, not a general object capability.
- **Constraints / negative claims:** no present claim of general object APIs, versioning, retention, legal hold, malware scanning, cross-profile portability, replication, or recovery. Provider choice must preserve the semantic contract.
- **Evidence:** current S3/EFS/filesystem inventory, `FRU-OBJECT-001` specification frontier, and absence of a general object authority or operation tests.

## Private deployment

### `CAP-PRIVATE-001` — Enforce secS-only private exposure

- **Domains / actors / outcome:** `DOM-DEP`; external callers can invoke only admitted semantic Hub operations, while every Hub runtime, state authority, and provider connection remains virtually private.
- **Inputs → outputs / side effects:** identity evidence and requested semantic operation → secS decision and verified context; accepted contexts dispatch to a private handler, while rejection occurs before any Hub domain side effect.
- **Components / interfaces:** `SEC-SECS-001`, future `FRU-SECS-ADAPTER-001`, receiver-local manifests, private service networking, ingress/firewall controls, and every capability handler. The integration FRU may wrap an imported package or a co-deployed service.
- **State / external dependencies:** secS owns admission decisions, replay/expiry controls, and verification receipts; Hub owns operation/domain state. Identity and wallet systems may supply evidence to secS but never directly authorize Hub.
- **Compositions / maturity:** private local, cloud, and on-premises targets are C0. Current Gateway, ALB/CloudFront, Matrix, and on-premises ingress paths expose Hub/Synapse without secS.
- **Constraints / negative claims:** “private” includes loopback, Unix sockets, private container/overlay networks, Kubernetes ClusterIP, or VPC-private subnets. TLS, API keys, Review Auth, wallet login, or network location alone do not satisfy the gate.
- **Evidence:** `private-exposure-boundary.md`, secS packet/manifest/context/receipt implementation at `8cf8e0a`, and current exposure-path audit.

## Operability

### `CAP-OPERATE-001` — Operate the private Hub

- **Domains / actors / outcome:** `DOM-ASR`; operators can deploy, verify, update, observe, back up, restore, and roll back a named private Hub composition with revision-bound evidence.
- **Inputs → outputs / side effects:** approved profile, images, configuration, secrets, target revision, operator action, and evidence policy → deployed or changed composition, health/telemetry, recovery result, and evidence; mutates only the named environment.
- **Components / interfaces:** private local/cloud/on-premises profiles, IaC, rollout/update controls, observability, backups, restore rehearsal, smoke/contract validators, and private-artifact scans.
- **State / external dependencies:** IaC state, operator state, logs/metrics/traces, backups, and evidence records have named authorities; target platform, registry, secret store, DNS, and the selected embedded/co-deployed secS packaging are explicit dependencies.
- **Compositions / maturity:** target complete private compositions are C0. Current deployment, validation, update, Matrix recovery, and evidence components have C2–C5 evidence in parts, but none proves the complete seven-capability secS-gated composition.
- **Constraints / negative claims:** no SLA, billing, service-credit, zero-downtime, unattended fleet, universal rollback, RPO/RTO, or production-readiness claim. Each proof is limited to its profile, revision, time, and declared invariant.
- **Evidence:** Compose/Terraform/Kubernetes profiles, validators and tests, rollout/update/recovery runbooks, historical Matrix evidence, and the current gap inventory.

## Claim maintenance

Any change to an outcome, semantic operation, component, state authority, external
dependency, private composition, constraint, or evidence reference requires claim
review. Future secS opcodes and machine registries must be additive, receiver-local,
and compatibility-reviewed. This documentation pass does not alter `ZenithPacket`
v0 or any persisted Queue, Cases, Matrix, Review, or evidence shape.
