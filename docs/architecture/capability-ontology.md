# Hub Capability Ontology

Hub has a deliberately small product claim surface. It is a virtually private
substrate whose externally reachable operations are admitted by secS-magik. The
eight top-level claims are DevGraph, Matrix, Queue, inference, object storage,
Hub Monitor, private exposure, and operability. Gateway, Cases, Frank, Runtime,
workers, indexers, and tools remain functional components beneath those claims.

This is the approved target contract, not a statement that the current repository
already meets it. The exposure rules and present gaps are defined in
[`private-exposure-boundary.md`](private-exposure-boundary.md); complete claims are
in [`capability-claims.md`](capability-claims.md); component identities remain in
[`functional-components.md`](functional-components.md).

## Core ontology

| Object | Definition | Identity rule |
|---|---|---|
| Capability | Bounded product outcome provided by the private Hub substrate | Stable `CAP-*` ID and one verb-object outcome |
| Capability domain | Family organizing related capability outcomes | Stable `DOM-*` ID; domains do not implement behavior |
| Functional component | Cohesive runtime, data, contract, policy, deployment, or operating element | One repository unit and one `FC-*` kind |
| Functional repository unit (FRU) | Smallest repository-owned unit that can be versioned, tested, deployed, replaced, or deprecated independently | Stable `FRU-*` ID; source paths may move without changing identity |
| Deployment composition | Named set of FRUs, dependencies, configuration, state authorities, network boundaries, and controls | Stable profile ID with explicit membership |
| secS operation | Receiver-owned semantic operation admitted by a secS manifest before Hub dispatch | Semantic name in documentation; machine opcode remains receiver-local |
| Verified call context | Principal, receiver, operation, packet identity, decision, expiry, and audit facts produced by successful secS verification | Hub accepts it only from its private secS adapter |
| State authority | Component or external system owning durable truth for a data class | Exactly one authority; caches and projections are identified separately |
| External dependency | Provider not implemented by Hub | Provider/protocol, adapter, failure boundary, retained responsibility |
| Evidence artifact | Reproducible proof tied to revision, subject, composition, validator, and time | Immutable reference; evidence does not float to newer revisions |
| Capability claim | Controlled statement of the strongest supported outcome | Capability, maturity, composition, evidence, constraints, and exclusions |

The governing rule is:

> External callers do not address Hub services directly. secS-magik makes the
> final admission decision, then dispatches a verified context to a private Hub
> operation. Hub owns the operation, state transition, and domain authorization
> that follows admission.

Identity wallets, browser login, Matrix identity, and other credential systems
may supply evidence to secS-magik. They do not bypass or replace the final secS
admission decision. Hub therefore has no top-level authentication capability.

## Capability domains

| Domain | Definition | Top-level claim |
|---|---|---|
| `DOM-GPH` Graph | Repository and work graph truth, relationships, policy-filtered reads, and change events | DevGraph |
| `DOM-MSG` Messaging | Matrix room, event, bridge, and administration behavior | Matrix |
| `DOM-WRK` Work | Durable work intake, leases, retries, settlement, and workflow coordination | Queue |
| `DOM-INF` Inference | Private model loading and inference execution | Inference server |
| `DOM-OBJ` Objects | Durable binary and document object lifecycle | Object storage |
| `DOM-OBS` Monitoring | Read-only deployment inventory, expected and observed topology, health, release, freshness, and drift | Hub Monitor |
| `DOM-DEP` Private deployment | Private composition, network isolation, and secS-only exposure | Private Hub boundary |
| `DOM-ASR` Operability | Deploy, verify, update, back up, restore, roll back, and preserve evidence | Operability |

Authentication and external admission are owned logically by secS-magik,
represented as required security substrate `SEC-SECS-001`, rather than as a Hub
capability domain. The substrate may be imported into the Hub receiver or
co-deployed with Hub; packaging does not change admission ownership.

## Functional-component kinds

| Kind | Definition | Examples |
|---|---|---|
| `FC-SVC` Service | Independently started process with a network or consumer boundary | Queue, Cases, Runtime, inference server |
| `FC-WRK` Worker | Long-running consumer claiming and settling asynchronous work | Frank, profile workers |
| `FC-LIB` Library | Imported cohesive behavior without a process lifecycle | common, knowledge, tool libraries |
| `FC-API` Private API facade | Internal interface translating or constraining a capability | private Gateway, Matrix administration facade |
| `FC-CON` Contract/schema | Versioned shape interpreted by multiple producers or consumers | protobuf, deployment profiles, model profiles |
| `FC-ADP` Adapter/integration | Translation between Hub and another package, process, provider, or protocol | embedded/co-deployed secS, DevGraph, Matrix, speech adapters |
| `FC-TOL` Tool | Registered bounded operation invoked by an agent or runtime | case and speech tools |
| `FC-AGT` Agent/policy package | Repository-owned agent identity, configuration, and rules | Frank, Sophia |
| `FC-PRC` Process/workflow | Declarative or compiled steps, inputs, and outputs | Review processes |
| `FC-STA` State authority | Durable store and schema owning truth for a data class | Queue database, object store |
| `FC-DEP` Deployment profile | Named composition of components and dependencies | local, cloud, on-premises |
| `FC-INF` Infrastructure module | Infrastructure-as-code resource graph | Terraform, Kubernetes |
| `FC-OPS` Operator control | Control that changes, verifies, restores, or inspects a composition | rollout, smoke, recovery |
| `FC-EVD` Evidence gate | Test or validator proving a bounded invariant | contract tests, artifact scan |
| `FC-SPC` Specification unit | Intended capability without current executable realization | future adapters and authorities |

## Relationship taxonomy

Every relationship is directed and typed. “Connected to” is not sufficient.

| Relationship | Meaning | Required metadata |
|---|---|---|
| `implements` | Component contributes behavior to a capability | Capability ID and bounded contribution |
| `admits` | secS accepts a semantic operation for a verified principal and receiver | Manifest operation, decision, context, expiry |
| `dispatches_to` | Private adapter forwards an admitted operation to its Hub handler | Operation mapping and failure semantics |
| `exposes` | Component publishes an internal interface | Protocol, operation, schema/version |
| `consumes` | Component reads an interface, event, file, or contract | Producer/interface and failure behavior |
| `produces` | Component emits data, events, artifacts, or transitions | Output schema and destination |
| `persists_to` | Component writes a named state authority | Data class, writer rule, consistency boundary |
| `reads_from` | Component reads authoritative or derived state | Authority and freshness rule |
| `depends_on` | Component requires another unit | Required/optional, startup/runtime, fallback |
| `deploys` | Profile or module creates or runs a component | Profile, environment, resource identity |
| `verifies` | Evidence gate checks a capability, component, or composition | Invariant, revision, result |
| `supersedes` | Unit or contract intentionally replaces another | Migration, compatibility window, removal state |

## Capability maturity

Maturity is recorded for one capability in one composition. Existing ungated
services may be mature as implementation components while the corresponding
secS-gated product claim remains only specified.

| Level | Name | Required evidence | Permitted claim |
|---|---|---|---|
| `C0` | Operation specified | Outcome, semantic operation, actors, inputs, outputs, constraints, and rejection boundary | “Hub intends to provide…” |
| `C1` | Private substrate exists | Handler, state authority, and private-only interface exist | “Hub implements the private substrate…” |
| `C2` | Verified | Contract and failure tests prove rejection before side effects and valid handler behavior | “Hub repository-verifies…” |
| `C3` | secS integrated | A secS manifest and adapter dispatch verified contexts through the real contract | “secS-gated Hub provides…” |
| `C4` | Privately deployable | IaC, isolation, configuration, state, recovery, and verification exist for a named composition | “Hub can deploy privately using profile X…” |
| `C5` | Operationally evidenced | A named deployed revision produced accepted point-in-time evidence | “Deployment X demonstrated…” |

Direct public Gateway or Synapse evidence cannot raise target maturity. `C5` is
point-in-time evidence, not an availability guarantee.

## Capability-claim contract

A complete controlled claim contains:

| Field | Required meaning |
|---|---|
| `capability_id`, `name` | Stable identity and concise verb-object label |
| `domain_ids`, `actors`, `outcome` | Domain, recipient, and observable result |
| `inputs`, `outputs`, `side_effects` | Typed boundary and mutations |
| `component_ids`, `interfaces` | Required FRUs and end-to-end operations |
| `state_authorities`, `external_dependencies` | Durable truth and delegated providers |
| `composition_ids`, `maturity_by_composition` | Named profiles and `C0`–`C5` evidence |
| `constraints`, `negative_claims` | Limits and nearby interpretations not supported |
| `evidence_refs`, `source_revision`, `owner`, `reviewed_at` | Proof, revision, accountability, and review date |

## Minimal capability registry

| Capability | Domain | Current evidence | Target maturity |
|---|---|---|---|
| `CAP-GRAPH-001` Provide DevGraph | `DOM-GPH` | DevGraph has a repository-verified internal graph substrate; Hub has no DevGraph or secS integration | `C0` |
| `CAP-MATRIX-001` Provide Matrix | `DOM-MSG` | Hub has direct Matrix implementations and deployment profiles, including historical evidence; they bypass secS | `C0` |
| `CAP-QUEUE-001` Provide durable Queue | `DOM-WRK` | Queue is integrated and deployable in current compositions; no secS operation boundary exists | `C0` |
| `CAP-INFER-001` Provide private inference | `DOM-INF` | Private inference/model-loading substrate exists in named profiles; no secS operation boundary exists | `C0` |
| `CAP-OBJECT-001` Provide object storage | `DOM-OBJ` | Narrow S3, EFS, and file uses exist; no general object authority or operation contract exists | `C0` |
| `CAP-MONITOR-001` Observe Hub deployments | `DOM-OBS` | Profiles, operator state, point health checks, a Frank process dashboard, and selected alarms exist; no unified read-only Hub Monitor exists | `C0` |
| `CAP-PRIVATE-001` Enforce secS-only private exposure | `DOM-DEP` | Current public Gateway, Matrix, AWS, and on-premises paths bypass secS | `C0` |
| `CAP-OPERATE-001` Operate the private Hub | `DOM-ASR` | Deployment, validation, update, observation, and recovery controls exist in parts; no complete private composition exists | `C0` |

## Machine-readable boundary

This documentation pass changes no machine contract. It defines semantic operation
names only; it does not allocate secS opcodes, alter `ZenithPacket` v0, or change
persisted Queue, Cases, Matrix, Review, or evidence shapes. Future machine-readable
registries must be additive and reviewed across producers, consumers, stored data,
deployment profiles, and rollback paths.
