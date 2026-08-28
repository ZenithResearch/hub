# Hub Capability Ontology

This document defines the vocabulary used to state what Hub can do. It is
capability-first: define a bounded outcome, identify the functional components
that produce it, name the composition in which they run, and limit the claim to
the available evidence.

Hub does not currently need a billing-driven service-level agreement. Availability,
recovery, security, and operating evidence remain part of relevant capabilities,
but contractual uptime, remedies, or service credits would be a separate optional
commercial layer.

## Core ontology

| Object | Definition | Identity rule |
|---|---|---|
| Capability | Bounded outcome Hub can produce for a user, operator, agent, or system | Stable `CAP-*` ID and one verb-object outcome |
| Capability domain | Family of outcomes sharing an architectural concern | Stable `DOM-*` ID; domains organize but do not implement |
| Functional component | Cohesive runtime, data, contract, policy, deployment, or operating element contributing behavior | One repository unit and one `FC-*` kind |
| Functional repository unit (FRU) | Smallest repository-owned component that can be versioned, tested, deployed, replaced, or deprecated independently | Stable `FRU-*` ID; source roots may move without changing identity |
| Deployment composition | Named set of FRUs, external dependencies, configuration, state authorities, and controls assembled for an environment | Stable profile ID with explicit membership |
| Interface | Typed boundary accepting input or exposing behavior | Protocol plus operation and schema identity |
| State authority | Component or external system owning durable truth for a data class | Exactly one authority; mirrors and caches are identified separately |
| External dependency | Capability provider not implemented by Hub | Provider/protocol, Hub adapter, failure boundary, retained responsibility |
| Evidence artifact | Reproducible proof tied to revision, subject, composition, validator, and time | Immutable reference; evidence does not float to newer releases |
| Capability claim | Controlled statement of the strongest supported outcome | Capability, maturity, composition, evidence, and exclusions |
| Constraint | Condition limiting scope, input, scale, mode, provider, security posture, or use | Attached directly to the claim |

The primary rule is:

> A capability claim says what outcome Hub produces, through which components
> and interfaces, in which composition, and under which constraints. The claim
> does not become broader because one component exists or one environment once
> passed a test.

## Capability domains

| Domain | Definition | Current families |
|---|---|---|
| `DOM-CTL` Control and access | Accept, authenticate, authorize, route, and expose interactions | Gateway API, Review access, HubFS, configuration |
| `DOM-WRK` Work orchestration | Represent, queue, coordinate, execute, and reconcile work | Queue, Cases, Eventbus, Frank, workers, processes |
| `DOM-EXE` Execution and tools | Perform bounded computation through models, tools, media processors, and sandboxes | Runtime, sandbox, registered tools, STT, model plane |
| `DOM-KNW` Knowledge and retrieval | Index, retrieve, and expose knowledge, process, and vault content | KB/process indexers, Qdrant, Vault surfaces |
| `DOM-MSG` Messaging and integration | Exchange events and messages with external systems and protocols | Matrix bridge/ingest, feeds, Synapse, MAS, Hypha |
| `DOM-DAT` Data and state | Own durable truth, persistence, retention, and artifact access | Queue/Cases stores, Review registry, files, Postgres |
| `DOM-IDN` Identity and policy | Define principals, permissions, credentials, roles, policy, and audit boundaries | Review Auth, rolodex, secret handles, Matrix identity |
| `DOM-DEP` Deployment and operations | Compose, configure, deploy, update, observe, back up, restore, and roll back | Compose, AWS, on-premises, manifests, runbooks |
| `DOM-ASR` Assurance and governance | Verify contracts and compositions, preserve evidence, and govern claims | Tests, validators, evidence bundles, artifact scans |
| `DOM-AGT` Agent and process content | Define agent identity, operating policy, skills, and process behavior | Frank, Sophia, worker skills, Review processes |

Domains and components are many-to-many. A component has one primary domain and
may contribute to secondary domains.

## Functional-component kinds

| Kind | Definition | Identity boundary | Examples |
|---|---|---|---|
| `FC-SVC` Service | Independently started process with a network or consumer boundary | Entrypoint, interface, configuration, dependencies, lifecycle | Gateway, Queue, Cases, Eventbus, Runtime, Sandbox, STT |
| `FC-WRK` Worker | Long-running consumer claiming and settling asynchronous work | Input, claim semantics, execution contract, settlement | Frank, Hermes worker queue |
| `FC-LIB` Library | Imported cohesive behavior without a process lifecycle | Public module boundary and consumers | `libs/common`, `libs/kb`, `libs/tools` |
| `FC-API` API facade | Interface layer translating or constraining another capability | Route/protocol schema and authorization boundary | Gateway admin APIs, Hypha broker |
| `FC-CON` Contract/schema | Versioned shape interpreted by multiple producers or consumers | Schema/protocol identity and compatibility policy | Protobuf, deployment/model profiles, tool manifests |
| `FC-ADP` Adapter/integration | Translation between Hub and an external provider or protocol | Upstream/downstream contracts and failure semantics | Matrix adapters, STT providers, Qdrant store |
| `FC-TOL` Tool | Registered bounded operation invoked by an agent or runtime | Manifest, inputs, outputs, permissions, timeout, side effects | Case tools, speech adapters, echo |
| `FC-AGT` Agent/policy package | Repository-owned identity, configuration, persona, and rules | Agent identity and owned content | Frank, Sophia |
| `FC-PRC` Process/workflow | Declarative or compiled steps, dependencies, inputs, and outputs | Process identity/version and stored snapshot | Queued Review, mock Review |
| `FC-STA` State authority | Durable store and schema owning truth for a data class | Data class, schema, writer, migration, recovery | Queue SQLite, Cases SQLite, Review Postgres |
| `FC-DEP` Deployment profile | Named composition of components and dependencies | Profile, membership, configuration, authorities, verification | Local Compose, AWS baseline/edge, Matrix EC2 |
| `FC-INF` Infrastructure module | IaC provisioning resources for one or more profiles | State/backend boundary and resource graph | AWS Terraform, on-premises Kubernetes |
| `FC-OPS` Operator control | Script or runbook changing, verifying, restoring, or inspecting a composition | Command, prerequisites, mutation, outputs, rollback | Update planner, rollout, smoke, restore rehearsal |
| `FC-EVD` Evidence gate | Test or validator proving a bounded invariant | Subject revision/profile, validator, result, artifact | Test suite, static manifests, Matrix evidence |
| `FC-SPC` Specification unit | Intended capability without current executable realization | Requirement and acceptance boundary | H tiers, Outbox, Planner, future modules |

## Relationship taxonomy

Every relationship is directed and typed. “Connected to” is not sufficient.

| Relationship | Meaning | Required metadata |
|---|---|---|
| `implements` | Component contributes behavior to a capability | Capability ID and bounded contribution |
| `exposes` | Component publishes an interface | Protocol, operation, schema/version |
| `consumes` | Component reads an interface, event, file, or contract | Producer/interface and failure behavior |
| `produces` | Component emits data, events, artifacts, or transitions | Output schema and destination |
| `persists_to` | Component writes a named state authority | Data class, writer rule, consistency boundary |
| `reads_from` | Component reads authoritative or derived state | Authority and freshness/consistency rule |
| `depends_on` | Component requires another unit | Required/optional, startup/runtime, fallback |
| `configures` | Contract or control selects component behavior | Schema and precedence |
| `deploys` | Profile or module creates/runs a component | Profile, mode, environment, resource identity |
| `verifies` | Evidence gate checks a capability, component, or composition | Invariant, subject revision, result |
| `externalizes` | Hub delegates underlying behavior outside the repository | Adapter, provider, retained responsibility |
| `supersedes` | Unit or contract intentionally replaces another | Migration, compatibility window, removal state |

## Capability maturity

Maturity is recorded for one capability in one composition. It is independent of
component lifecycle.

| Level | Name | Required evidence | Permitted claim |
|---|---|---|---|
| `C0` | Specified | Outcome, actors, inputs, outputs, constraints, acceptance boundary | “Hub intends to provide…” |
| `C1` | Implemented | Current code/content and interfaces exist | “Hub implements…” |
| `C2` | Verified | Relevant unit, contract, or static checks pass at a named revision | “Hub repository-verifies…” |
| `C3` | Integrated | Required components exchange real contracts in a named composition | “Hub provides this capability in composition X…” |
| `C4` | Deployable | Profile/IaC, configuration, state, operations, and verification path exist | “Hub can deploy this capability using profile X…” |
| `C5` | Operationally evidenced | A named deployed revision produced accepted point-in-time evidence | “Deployment X demonstrated this capability at revision/time Y…” |

`C5` is not a guarantee of future availability. Each level inherits all prior
requirements, and maturity can differ between local, AWS, Matrix-only, and
on-premises compositions.

## Capability-claim contract

A complete controlled claim contains:

| Field | Required meaning |
|---|---|
| `capability_id`, `name` | Stable identity and concise verb-object label |
| `domain_ids` | Primary and secondary capability domains |
| `actors`, `outcome` | Recipient and observable result |
| `inputs`, `outputs`, `side_effects` | Typed boundaries and mutations |
| `component_ids` | Required and optional FRUs |
| `interfaces` | Operations and schemas traversed end to end |
| `state_authorities` | Durable truth for every affected data class |
| `external_dependencies` | Provider/protocol and retained Hub responsibility |
| `composition_ids` | Profiles in which the capability is integrated or deployable |
| `maturity_by_composition` | `C0`–`C5`, each with evidence references |
| `constraints`, `negative_claims` | Limits and nearby interpretations not supported |
| `evidence_refs`, `source_revision` | Proof and exact revision assessed |
| `owner`, `reviewed_at` | Accountable maintainer and review date |

A capability is fully defined only when every field above is explicit. A local-only
capability can be fully defined and implemented while remaining absent from AWS or
on-premises profiles.

## Current capability registry

Full claim records are in [`capability-claims.md`](capability-claims.md). Component
definitions and evidence boundaries are in
[`functional-components.md`](functional-components.md).

| Capability | Domains | Required FRUs | Current bounded maturity and claim |
|---|---|---|---|
| `CAP-CTL-001` Route Hub API operations | `DOM-CTL` | `FRU-EDGE-001` plus called services | C3 local; C4 named AWS profiles. Routes documented Gateway operations to current internal services and bounded file/state surfaces |
| `CAP-AUTH-001` Control Review access | `DOM-IDN`, `DOM-CTL` | `FRU-AUTH-001`, `FRU-EDGE-001` | C2 repository; C4 AWS RDS option. Implements Review-scoped policy and sessions, not universal Hub IAM |
| `CAP-FS-001` Read Hub files and artifacts | `DOM-CTL`, `DOM-DAT` | `FRU-FS-001`, `FRU-EDGE-001` | C3 mounted local/AWS compositions. Exposes allowlisted operations, not object-store lifecycle |
| `CAP-WRK-001` Queue durable work | `DOM-WRK`, `DOM-DAT` | `FRU-QUEUE-001`, `FRU-PROTO-001` | C3 local; C4 AWS single-writer. Persists and settles queued work with leases, retry, and idempotency |
| `CAP-CASE-001` Persist workflow execution state | `DOM-WRK`, `DOM-DAT` | `FRU-CASE-001` | C3 local; C4 AWS single-writer. Records canonical cases, steps, slots, runs, events, artifacts, and projections |
| `CAP-EVT-001` Distribute runtime events | `DOM-WRK` | `FRU-EVENT-001` | C3 local/AWS. Distributes HTTP events while running; not a durable replay log |
| `CAP-FRANK-001` Execute native case pipelines | `DOM-WRK`, `DOM-AGT` | `FRU-FRANK-001`, `FRU-CASE-001`, `FRU-QUEUE-001`, `FRU-PROCESS-001`, `FRU-TOOLS-001` | C3 local; C4 AWS. Supports only `native_case_pipeline`; `direct` and `kanban` are rejected |
| `CAP-WORKER-001` Launch bounded profile workers | `DOM-WRK`, `DOM-AGT` | `FRU-WORKER-001`, Queue/Cases/Eventbus | C3 local. No current fleet-autoscaling or AWS-parity claim |
| `CAP-MODEL-001` Resolve model bindings | `DOM-EXE`, `DOM-IDN` | `FRU-MODEL-001`, `FRU-COMMON-001` | C2 repository; C4 named profiles. Validates bindings and secret handles, not provider quality or availability |
| `CAP-TOOL-001` Invoke registered tools | `DOM-EXE` | `FRU-RUNTIME-001`, `FRU-SANDBOX-001`, `FRU-TOOLS-001` | C3 local/AWS core. Applies declared timeout, memory, and network controls |
| `CAP-STT-001` Transcribe Review audio | `DOM-EXE` | `FRU-STT-001`, speech adapters | C3 local; C4 AWS. Normalizes configured providers with guarded file roots; no accuracy or latency promise |
| `CAP-KNW-001` Index and search knowledge | `DOM-KNW` | `FRU-KB-001`, `FRU-KBLIB-001`, Qdrant | C3 local; AWS integration code is C2. Production Qdrant provisioning/recovery is incomplete |
| `CAP-PRC-001` Compile process contracts | `DOM-WRK`, `DOM-AGT` | `FRU-PROCESS-001`, `FRU-PROCINDEX-001` | C3 local. Parses supported Markdown processes into steps, edges, slots, assignments, and packets |
| `CAP-MSG-001` Exchange Matrix transactions | `DOM-MSG` | Matrix bridge/ingest and a Synapse profile | C3 local; C4 AWS profiles; one historical C5 evidence bundle. Claims are profile- and revision-specific |
| `CAP-MSG-ADM-001` Administer Matrix through a facade | `DOM-MSG`, `DOM-IDN` | `FRU-HYPHA-001`, Synapse Admin API | C2 repository; C4 standalone-profile code. Does not expose persistent admin credentials to clients |
| `CAP-DEP-LOCAL-001` Run the integrated local topology | `DOM-DEP` | `FRU-COMPOSE-001` and listed members | C4 development composition. Not a production-HA or on-premises-product claim |
| `CAP-DEP-AWS-001` Provision Hub on AWS | `DOM-DEP` | `FRU-AWS-BASE-001` or `FRU-AWS-EDGE-001` | C4 by profile. Each claim is limited to explicit Terraform membership; profiles do not imply parity |
| `CAP-DEP-MATRIX-001` Provision standalone Matrix | `DOM-DEP`, `DOM-MSG` | `FRU-MATRIX-EC2-001`, `FRU-FRESH-RECOVERY-001` | C4. IaC/operator path exists; no claim that an instance is currently running |
| `CAP-DEP-ONPREM-001` Provision on-premises core prototype | `DOM-DEP` | `FRU-ONPREM-001` | C1/C4 prototype. Deploys Gateway, Runtime, and Sandbox only; not full-product parity |
| `CAP-UPD-001` Plan operator-controlled updates | `DOM-DEP` | `FRU-UPDATE-001` | C2 repository. Plans and records controlled updates; not unattended fleet management |
| `CAP-REC-001` Rehearse Matrix recovery | `DOM-DEP`, `DOM-ASR` | `FRU-FRESH-RECOVERY-001`, Matrix snapshot controls | C4 current profile; historical C5 for accepted ECS/RDS/EFS run. Evidence is revision-specific |
| `CAP-ASR-001` Verify repository contracts | `DOM-ASR` | `FRU-TEST-001`, validators | C2 current repository. Does not imply live environmental conformance |

## Machine-readable boundary

This documentation pass does not change a machine contract. Future additions may
introduce `infra/capabilities.yaml` and `infra/functional-units.yaml`, but only as
additive schemas after producer, consumer, persisted-evidence, compatibility, and
migration review. Generated documentation should eventually prevent the Markdown
and machine registries from drifting.
