# Hub Capability Claims

This catalogue expands every current `CAP-*` record into a complete bounded claim.
The vocabulary and maturity model are defined in
[`capability-ontology.md`](capability-ontology.md); concrete component definitions
are in [`functional-components.md`](functional-components.md).

Unless a record says otherwise, the assessment revision is
`a6235b6c764f14fa9d97c262ba19ae1fc9df0264`, the review date is `2026-08-27`,
and the accountable runtime owner is **unassigned**. Unassigned ownership is a
documentation gap, not permission to omit an owner from a future machine registry.
The Frank/AWS narrative cleanup is additionally assessed on branch
`codex/frank-runtime-cleanup` at commits `847de04` and `26a99a0`.

Composition names such as `matrix-local`, `matrix-aws-ecs`, `matrix-aws-ec2`,
and `onprem-k8s-prototype` are documentation identities. They do not add or alter
the current `infra/deployment-profiles.yaml` contract.

## Control and access

### `CAP-CTL-001` — Route Hub API operations

- **Domains / actors / outcome:** `DOM-CTL`; users, ZenithOS/Review clients, operators, and internal services receive authenticated HTTP access to the current Hub control surfaces.
- **Inputs → outputs / side effects:** HTTP requests, sessions, tokens, route payloads, and file/process identifiers → JSON/SSE/file responses; may enqueue work, call internal services, update Review/model configuration, or read/write bounded state.
- **Components / interfaces:** `FRU-EDGE-001` required; `FRU-AUTH-001`, `FRU-FS-001`, Runtime, Queue, Cases, Eventbus, STT, Matrix, and model-profile controls by route. HTTP/SSE fan out to current internal HTTP/gRPC contracts.
- **State / external dependencies:** Review registry and mounted file/artifact authorities by route; internal service availability, configured model/STT providers, and Matrix are explicit dependencies.
- **Compositions / maturity:** `local-dev` C3; `cloud-aws-staging` and `cloud-aws-prod` C4 for explicit Gateway/profile membership; AWS edge C4 for its narrower route dependencies.
- **Constraints / negative claims:** authentication and route-specific policy apply; not every route is available in every profile. This is not a stable public API, complete multi-tenant isolation, or evidence that all downstream services are live.
- **Evidence:** `services/gateway_http/`, `docs/gateway-http.md`, Gateway/session tests, Compose, AWS Terraform, deployment and image-manifest validators.

### `CAP-AUTH-001` — Control Review access

- **Domains / actors / outcome:** `DOM-IDN`, `DOM-CTL`; Review users and operators receive project/deployment/access-code policy enforcement and bounded sessions.
- **Inputs → outputs / side effects:** clients, projects, deployments, codes, origins/subjects, policy and session requests → preflight/session/admin results; mutates Review registry rows and audit timestamps.
- **Components / interfaces:** `FRU-AUTH-001` and `FRU-EDGE-001`; Gateway Review admin/session HTTP routes and SQLite/Postgres store abstraction.
- **State / external dependencies:** Review Auth registry is authoritative; optional AWS RDS/managed-secret path is external infrastructure operated through Hub IaC.
- **Compositions / maturity:** repository C2; `local-dev` C3; AWS baseline C4 when the clients Postgres option is selected.
- **Constraints / negative claims:** scope is Review access, not all Hub principals/services. No claim of universal IAM, SSO/MFA, complete tenant isolation, or completed security assurance.
- **Evidence:** Review Auth implementation, session/policy/Postgres tests, Gateway docs, AWS RDS/image-manifest contracts.

### `CAP-FS-001` — Read Hub files and artifacts

- **Domains / actors / outcome:** `DOM-CTL`, `DOM-DAT`; authorized clients and operators can inspect allowlisted repository, mirror, Review, and artifact paths.
- **Inputs → outputs / side effects:** stat/list/content/manifest/by-path requests → metadata or file bytes; reads configured roots and registered artifacts without making arbitrary filesystem paths public.
- **Components / interfaces:** `FRU-FS-001`, `FRU-EDGE-001`; Gateway HubFS, mirror, and artifact HTTP routes.
- **State / external dependencies:** configured filesystem/EFS roots are authoritative for exposed bytes; their durability and backup are profile/operator responsibilities.
- **Compositions / maturity:** `local-dev` C3; AWS mounted Gateway compositions C3/C4 for the named mount and route path.
- **Constraints / negative claims:** root allowlists, authentication, path normalization, and mounted-file availability apply. This is not general object storage, retention/legal hold, malware scanning, or replicated file durability.
- **Evidence:** Gateway file/artifact routes and tests, `tests/test_hubfs_iac.py`, Compose volumes, AWS EFS/mount configuration.

## Work orchestration

### `CAP-WRK-001` — Queue durable work

- **Domains / actors / outcome:** `DOM-WRK`, `DOM-DAT`; producers, workers, and operators can persist, claim, settle, retry, inspect, and dead-letter asynchronous work.
- **Inputs → outputs / side effects:** queue/message IDs, payloads, idempotency keys, claim timeouts, ack/nack results → messages and queue state; writes leases, attempts, status, errors, and timestamps.
- **Components / interfaces:** `FRU-QUEUE-001`, `FRU-PROTO-001`; Queue HTTP and gRPC contracts over the SQLite store.
- **State / external dependencies:** Queue SQLite is authoritative; local volumes or AWS EFS host the file; one active writer is required.
- **Compositions / maturity:** `local-dev` C3; `self-hosted-single-node` C3/C4 by documented profile; AWS baseline C4 single-writer.
- **Constraints / negative claims:** SQLite/EFS consistency and single-writer limits apply. No replicated HA, multi-region durability, zero-loss failover, or horizontal Queue scaling claim.
- **Evidence:** `inbox/`, Queue protocol, Queue HTTP tests, deployment-profile validator, Compose, AWS ECS/EFS definitions.

### `CAP-CASE-001` — Persist workflow execution state

- **Domains / actors / outcome:** `DOM-WRK`, `DOM-DAT`; agents, tools, operators, and clients receive a canonical record of cases and their execution.
- **Inputs → outputs / side effects:** case/step/slot/run/span/event/artifact/log/status operations → canonical records, streams, board projections, and completion outputs; mutates Cases SQLite tables.
- **Components / interfaces:** `FRU-CASE-001`; Cases HTTP API consumed by Frank, tools, Gateway, and worker paths.
- **State / external dependencies:** Cases SQLite is authoritative; local volume or AWS EFS; one active writer.
- **Compositions / maturity:** `local-dev` C3; `self-hosted-single-node` C3/C4; AWS baseline C4 single-writer.
- **Constraints / negative claims:** current schema/HTTP semantics and single-writer profile apply. No replicated HA, automatic failover, general workflow engine compatibility, or complete retention/deletion policy.
- **Evidence:** Cases implementation/README, contract and observability tests, case-tool tests, Compose and AWS task/mount definitions.

### `CAP-EVT-001` — Distribute runtime events

- **Domains / actors / outcome:** `DOM-WRK`; services and workers can publish and receive low-latency runtime events while Eventbus is running.
- **Inputs → outputs / side effects:** topic/event payload and subscriber request → delivered HTTP event stream/responses; mutates only in-process subscription/buffer state.
- **Components / interfaces:** `FRU-EVENT-001`; HTTP `publish`, `subscribe`, and `health`.
- **State / external dependencies:** no durable state authority; process memory is transient.
- **Compositions / maturity:** `local-dev` C3; AWS baseline C3/C4 as an explicitly deployed service.
- **Constraints / negative claims:** subscribers must tolerate interruption/loss. Eventbus is not a durable broker, ordered event log, replay store, or cross-region bus.
- **Evidence:** Eventbus service/README, broker tests, Compose and AWS ECS definitions.

### `CAP-FRANK-001` — Execute native case pipelines

- **Domains / actors / outcome:** `DOM-WRK`, `DOM-AGT`; Review users/operators receive process-driven case execution and reconciled completion/failure state.
- **Inputs → outputs / side effects:** Queue dispatch plus process/case/model/tool context → step runs, slots, logs, artifacts, status, output, and events; claims Queue work and mutates Cases through APIs.
- **Components / interfaces:** `FRU-FRANK-001`, `FRU-AGENT-FRANK-001`, Queue, Cases, Eventbus, `FRU-PROCESS-001`, `FRU-TOOLS-001`, model bindings, STT where selected.
- **State / external dependencies:** Cases is execution authority; Queue is work authority; filesystem execution artifacts and external model/STT/tool providers are bounded dependencies.
- **Compositions / maturity:** `local-dev` C3; AWS baseline C4 for the current native pipeline path.
- **Constraints / negative claims:** only `native_case_pipeline` is a valid current runtime; process/tool/model constraints apply. `direct` and `kanban` are rejected, and output quality is not invariant across models/inputs.
- **Evidence:** Frank service/README and native-pipeline docs, process/case tests, 77-test focused cleanup verification, Compose/AWS definitions, cleanup commit `847de04`.

### `CAP-WORKER-001` — Launch bounded profile workers

- **Domains / actors / outcome:** `DOM-WRK`, `DOM-AGT`; Hub operators can have a worker claim an assignment and launch a bounded Hermes-compatible execution profile.
- **Inputs → outputs / side effects:** worker-queue message and profile/case context → launched worker result, settlement, logs/events, and case updates; may start a local Docker worker container.
- **Components / interfaces:** `FRU-WORKER-001`, Queue, Cases, Eventbus, STT/Gateway, build-only `hermes-worker` image.
- **State / external dependencies:** Queue and Cases are authoritative; Docker socket/host runtime and configured model providers are dependencies.
- **Compositions / maturity:** `local-dev` C3; no named AWS baseline worker service.
- **Constraints / negative claims:** local host/Docker boundary and declared worker profiles apply. No managed worker fleet, autoscaling, hostile-tenant isolation, or AWS parity claim.
- **Evidence:** worker service/README, worker tests, Docker Compose and worker image definitions.

### `CAP-PRC-001` — Compile process contracts

- **Domains / actors / outcome:** `DOM-WRK`, `DOM-AGT`; agents and operators receive executable step/edge/slot/assignment structures from supported process Markdown.
- **Inputs → outputs / side effects:** process Markdown/frontmatter → parsed process, DAG, required slots, dispatch packet, and index records; may persist a dispatch snapshot or Qdrant document.
- **Components / interfaces:** `FRU-PROCESS-001`, optional `FRU-PROCINDEX-001`; Markdown contract and internal compiler/loader APIs.
- **State / external dependencies:** repository process files are source; Cases may persist dispatch snapshots; Qdrant is derived search state.
- **Compositions / maturity:** native pipeline C3; local process-indexing composition C3.
- **Constraints / negative claims:** only current grammar and shipped processes are supported. No visual planner, arbitrary workflow language, or guarantee for unvalidated future process types.
- **Evidence:** process files/index, process contract tests, Frank pipeline tests, process-indexer service/README.

## Execution and knowledge

### `CAP-MODEL-001` — Resolve model bindings

- **Domains / actors / outcome:** `DOM-EXE`, `DOM-IDN`; agents and operators receive effective provider/model/runtime bindings without embedding raw secrets in the profile contract.
- **Inputs → outputs / side effects:** agent, purpose, deployment profile, base contract, overrides, and secret handles → effective binding/connectivity result; writes bounded override and audit records through Gateway controls.
- **Components / interfaces:** `FRU-MODEL-001`, `FRU-COMMON-001`, Gateway model-admin routes; YAML/profile resolver APIs.
- **State / external dependencies:** base YAML plus overrides/audit files are authorities by precedence; provider endpoints and secret managers are external dependencies.
- **Compositions / maturity:** repository C2; `local-dev` and named AWS profiles C4 for configured binding paths.
- **Constraints / negative claims:** safe handles and declared profiles only. Does not prove provider reachability, model quality, capacity, latency, cost, or data handling.
- **Evidence:** model-profile contract/resolver tests, model/IaC tests, validator, image/environment manifest, Gateway routes.

### `CAP-TOOL-001` — Invoke registered tools

- **Domains / actors / outcome:** `DOM-EXE`; agents and runtime clients can invoke a registered bounded operation and receive structured output or failure.
- **Inputs → outputs / side effects:** tool name and validated arguments → declared result/error; may call Cases, Gateway, STT, files, or a provider according to the tool implementation.
- **Components / interfaces:** `FRU-RUNTIME-001`, `FRU-SANDBOX-001`, `FRU-TOOLS-001`; Runtime/Sandbox gRPC and tool manifests/contracts.
- **State / external dependencies:** each tool declares its state/external boundary; Cases is authoritative for case tools; speech providers are external for provider tools.
- **Compositions / maturity:** `local-dev` C3; AWS core profiles C3/C4 for registered tools included in the image and allowed by policy.
- **Constraints / negative claims:** registry, input schema, timeout, memory, and network flags apply. This is not arbitrary safe code execution or a hardened multi-tenant compute sandbox.
- **Evidence:** tool contracts/registry, Sandbox/Runtime services, case/speech tool tests, Compose/AWS policy configuration.

### `CAP-STT-001` — Transcribe Review audio

- **Domains / actors / outcome:** `DOM-EXE`; Frank, tools, and authorized workflows receive normalized text from an allowed audio file through a selected provider path.
- **Inputs → outputs / side effects:** allowlisted file path, model/provider/fallback configuration → transcript and provider metadata/error; reads audio and may call an external API.
- **Components / interfaces:** `FRU-STT-001`, local/OpenAI/ElevenLabs tool adapters, Frank STT client; HTTP `health`/`transcribe`.
- **State / external dependencies:** audio files remain authoritative; provider services/secrets are external; no transcript store is owned by STT itself.
- **Compositions / maturity:** `local-dev` C3; AWS baseline C4 for the service/configuration path.
- **Constraints / negative claims:** allowed roots/models, provider configuration, file format, and fallback apply. No accuracy, latency, language coverage, retention, or provider-availability guarantee.
- **Evidence:** STT service/README, Frank client and speech-tool tests, Compose/AWS manifest and deployment configuration.

### `CAP-KNW-001` — Index and search knowledge

- **Domains / actors / outcome:** `DOM-KNW`; runtime clients and agents receive vector-search results over indexed supported documents.
- **Inputs → outputs / side effects:** source documents/chunks and search queries → embeddings, Qdrant records, ranked results; index operations mutate the configured collection.
- **Components / interfaces:** `FRU-KBLIB-001`, `FRU-KB-001`, Runtime search; optional process/vault indexers; Qdrant API.
- **State / external dependencies:** source files are authoritative; Qdrant collection is derived state; embedding and Qdrant providers are dependencies.
- **Compositions / maturity:** `local-dev` C3; AWS integration code C2/C3 where a Qdrant endpoint is supplied, without complete provisioned parity.
- **Constraints / negative claims:** configured embedding/vector dimensions, collection, source readers, and endpoint apply. No complete AWS Qdrant hosting, backup/restore, tenant isolation, freshness, or retrieval-quality guarantee.
- **Evidence:** knowledge library/indexer/Runtime code, service READMEs, Compose Qdrant, model/image profile validators and tests.

## Messaging and administration

### `CAP-MSG-001` — Exchange Matrix transactions

- **Domains / actors / outcome:** `DOM-MSG`; Matrix users, appservices, Hub workers, and operators can exchange supported Matrix transactions with Hub work/event paths.
- **Inputs → outputs / side effects:** Matrix client/federation/appservice transactions and Hub events/work → Matrix responses/messages or Queue/Eventbus activity; mutates Synapse state and may enqueue Hub work.
- **Components / interfaces:** Synapse in `FRU-MATRIX-LOCAL-001`, `FRU-MATRIX-ECS-001`, or `FRU-MATRIX-EC2-001`; bridge, ingest, Gateway receiver, bots/appservice configuration.
- **State / external dependencies:** Synapse Postgres/media/signing/appservice material is authoritative; Matrix clients/federation peers are external; Hub Queue/Cases remain separate authorities.
- **Compositions / maturity:** `matrix-local` C3; `matrix-aws-ecs` and `matrix-aws-ec2` C4; one accepted `matrix-aws-ecs` revision has point-in-time C5 evidence.
- **Constraints / negative claims:** each profile has different membership and authority; ingest is gated. No automatic evidence for current `main`, every client/federation peer, or every Hub service.
- **Evidence:** Matrix services/IaC/runbooks/static tests, accepted `docs/evidence/matrix-production/` bundle for source `e62dfd9`.

### `CAP-MSG-ADM-001` — Administer Matrix through a facade

- **Domains / actors / outcome:** `DOM-MSG`, `DOM-IDN`; authorized native clients/operators receive short-lived typed Matrix administration without holding persistent Synapse admin credentials.
- **Inputs → outputs / side effects:** broker credentials/session plus typed user/room/password/purge operations → snapshots/results; mutates Synapse through its Admin API and keeps broker sessions in memory.
- **Components / interfaces:** `FRU-HYPHA-001`, Synapse Admin API, bootstrap/secret/image/deploy/rotation controls.
- **State / external dependencies:** Synapse is user/room authority; broker sessions are memory-only; Secrets Manager/SSM/ECR/AWS host are deployment dependencies.
- **Compositions / maturity:** repository C2; standalone/AWS deployment path C4 in code; no accepted live-operation evidence.
- **Constraints / negative claims:** bounded typed routes, secret/verifier/session rules, and explicit deployment apply. No direct persistent admin token exposure, no durable identity authority, and no claim that the broker image is currently deployed.
- **Evidence:** broker implementation and Matrix tests, operations/deployment docs, image/provenance and secret-rotation controls.

## Deployment and assurance

### `CAP-DEP-LOCAL-001` — Run the integrated local topology

- **Domains / actors / outcome:** `DOM-DEP`; developers/operators can start the broad local Hub service graph for integration and functional verification.
- **Inputs → outputs / side effects:** repository checkout, environment configuration, Docker network/volumes → running Compose services; creates containers, volumes, local database/vector state, and bound ports.
- **Components / interfaces:** `FRU-COMPOSE-001` and its explicit services/supporting Postgres/Qdrant members; Docker Compose contract.
- **State / external dependencies:** named Docker volumes and mounted repository/runtime paths; Docker and external `agentnet` are required.
- **Compositions / maturity:** `local-dev` C4 development composition; Matrix is a separate local composition.
- **Constraints / negative claims:** local secrets/defaults and development operating posture apply. Not production HA, a complete on-premises product, or evidence of cloud parity.
- **Evidence:** `docker-compose.yml`, service READMEs, `docker compose config`, profile validator, repository tests.

### `CAP-DEP-AWS-001` — Provision Hub on AWS

- **Domains / actors / outcome:** `DOM-DEP`; operators can provision the resource graph explicitly defined by a named AWS profile and follow controlled rollout/smoke paths.
- **Inputs → outputs / side effects:** Terraform/provider/profile/image/secret inputs → VPC, compute, routing, storage, database, secret, logging, and related resources; changes cloud state only on explicit apply.
- **Components / interfaces:** `FRU-AWS-BASE-001` or `FRU-AWS-EDGE-001`, profile/image contracts, rollout/build/smoke controls; Terraform/AWS APIs.
- **State / external dependencies:** Terraform state and AWS resources are authoritative for infrastructure; AWS account, quotas, region, IAM, providers, and operator credentials are external.
- **Compositions / maturity:** `cloud-aws-staging`/`cloud-aws-prod` baseline C4 for explicit members; AWS edge C4 for its narrower graph.
- **Constraints / negative claims:** prerequisites, profile membership, state backend, secret/image inputs, and manual approval apply. The legacy baseline path is not a price, service-count, capacity, parity, availability, or readiness promise.
- **Evidence:** Terraform, deployment/image/model manifests and validators, rollout/smoke docs/scripts, Terraform format checks; narrative cleanup commit `26a99a0`.

### `CAP-DEP-MATRIX-001` — Provision standalone Matrix

- **Domains / actors / outcome:** `DOM-DEP`, `DOM-MSG`; operators can provision and initialize a fresh, separately scoped Matrix homeserver on AWS.
- **Inputs → outputs / side effects:** Terraform/profile/secrets/domain/image inputs → VPC, EC2/EIP, Caddy, Synapse, Postgres/EBS, SSM, DLM, and verification/recovery controls; mutates AWS only on explicit apply/deploy.
- **Components / interfaces:** `FRU-MATRIX-EC2-001`, `FRU-FRESH-RECOVERY-001`; Terraform, SSM, Secrets Manager, Matrix endpoints, deployment/verification scripts.
- **State / external dependencies:** Terraform state, EC2/EBS/Postgres/secret state; AWS and public DNS/TLS dependencies.
- **Compositions / maturity:** `matrix-aws-ec2` C4; no current-live C5 claim.
- **Constraints / negative claims:** account/domain/image/secrets prerequisites and operator sequence apply. No claim of an existing live instance, Hub-core parity, or automatic bridge/ingest integration.
- **Evidence:** `infra/matrix/aws/`, deploy/bootstrap/verify/restore scripts, Matrix static and focused tests, runbooks.

### `CAP-DEP-ONPREM-001` — Provision an on-premises core prototype

- **Domains / actors / outcome:** `DOM-DEP`; an operator can deploy the present Gateway/Runtime/Sandbox Kubernetes skeleton to private infrastructure.
- **Inputs → outputs / side effects:** Kubernetes manifests, images, configuration, and cluster → three running workloads/services; mutates cluster resources.
- **Components / interfaces:** `FRU-ONPREM-001`; Kubernetes API and the three current service interfaces.
- **State / external dependencies:** no complete product state plane is defined; operator cluster, networking, storage, images, and secrets are external prerequisites.
- **Compositions / maturity:** `onprem-k8s-prototype` C1/C4 for the three declared components only.
- **Constraints / negative claims:** prototype membership is the boundary. No Queue, Cases, Frank, STT, Postgres/Qdrant, Matrix, backup, restore, observability, upgrade, or full-product parity claim.
- **Evidence:** `infra/onprem/k8s/agent-platform.yaml`, on-premises README, repository inspection.

### `CAP-UPD-001` — Plan operator-controlled updates

- **Domains / actors / outcome:** `DOM-DEP`; operators can compare desired source/profile with recorded node state and apply through an explicit guarded flow.
- **Inputs → outputs / side effects:** target ref/profile, operator-state document, backend/smoke prerequisites → plan or dry-run/apply result; records state only after successful smoke where supported.
- **Components / interfaces:** `FRU-UPDATE-001`, rollout/smoke controls; CLI, Git metadata, JSON operator state, provider preflights.
- **State / external dependencies:** operator-state JSON is the local record; Git source and cloud/provider state are external authorities for their domains.
- **Compositions / maturity:** repository C2; operator-managed composition E3 path where prerequisites are met.
- **Constraints / negative claims:** explicit operator approval and profile-specific preflight apply. Not unattended fleet orchestration, automatic production mutation, or universal application/data rollback.
- **Evidence:** update script/tests, example state, operator-update and source-ledger docs.

### `CAP-REC-001` — Rehearse Matrix recovery

- **Domains / actors / outcome:** `DOM-DEP`, `DOM-ASR`; operators can create coordinated Matrix backups, restore into an isolated target, verify it, and retain a bounded evidence result.
- **Inputs → outputs / side effects:** source profile/revision, database/storage snapshot inputs, restore target, validators → restored isolated composition, timing/results, and evidence; creates and cleans rehearsal resources under guardrails.
- **Components / interfaces:** `FRU-FRESH-RECOVERY-001`, `FRU-MATRIX-EVID-001`, profile-specific snapshot/backup controls and restore runbooks.
- **State / external dependencies:** source Matrix state remains authoritative; AWS RDS/EFS or EBS/DLM snapshots are recovery copies; AWS APIs and operator credentials are external.
- **Compositions / maturity:** fresh Matrix profile C4; accepted ECS/RDS/EFS evidence bundle is point-in-time C5 for source `e62dfd9`, not current `main`.
- **Constraints / negative claims:** profile/data classes, timestamp, revision, validator, redaction, isolation, and cleanup apply. No universal Hub restore, continuous RPO/RTO, or future-run guarantee.
- **Evidence:** Matrix backup/restore scripts and tests, recovery runbooks, accepted production evidence JSON and validator.

### `CAP-ASR-001` — Verify repository contracts

- **Domains / actors / outcome:** `DOM-ASR`; developers, reviewers, and operators receive reproducible pass/fail evidence for bounded code, static contracts, manifests, and infrastructure invariants.
- **Inputs → outputs / side effects:** repository revision, tests, validators, configuration, and fixtures → reports, failures, and optional evidence files; may create only local test/cache artifacts unless a specific operator test says otherwise.
- **Components / interfaces:** `FRU-TEST-001`, profile/model/image/Matrix validators, Terraform format checks, Compose rendering, private-artifact scan.
- **State / external dependencies:** repository revision and fixtures are authoritative; toolchain versions are dependencies; live provider state is excluded unless explicitly named.
- **Compositions / maturity:** current repository C2. Individual deployment profiles may reach higher maturity only through their own operational evidence.
- **Constraints / negative claims:** each validator proves only its declared invariant at the tested revision. Passing tests is not production availability, complete security assurance, current cloud state, or every capability's C5 evidence.
- **Evidence:** `tests/`, validation scripts, CI workflow, documented command results; the capability-doc branch passed 500 tests, 20 subtests, Compose/profile/model/image/Matrix checks, and Terraform formatting with 10 pre-existing marker warnings.

## Claim maintenance

Every change to an outcome, required component, interface, state authority, external
dependency, composition, constraint, or evidence reference requires review of the
corresponding claim. New claims must use the same complete record, including an
explicit unassigned owner if assignment is not yet known. A future machine registry
must be additive and must preserve compatibility with existing deployment profiles
and evidence readers.
