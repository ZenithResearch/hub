# Hub Monitor MVP

Hub Monitor is the target read-only infrastructure view intended to ship with Hub.
Its primary user is an owner or CEO who needs to understand what Hub deployments
exist, where they are expected to run, whether they are actually present, and which
conditions need technical attention.

This is a target specification, not an implementation claim. The current Frank
dashboard, deployment profiles, operator-state example, health endpoints,
production smoke scripts, and selected CloudWatch alarms are useful inputs, but
they do not form Hub Monitor.

## Product boundary

Hub Monitor answers four questions in plain language:

1. What deployments am I watching?
2. Which machine is each deployment expected to be on?
3. What is actually running, at which release, and how fresh is the observation?
4. What differs from the expected picture or needs attention?

“Visual binary editor” means an on/off editor for **monitoring scope**. Toggling a
deployment changes whether Hub Monitor observes and displays it. Assigning a
machine records the expected location used for comparison. Neither action changes
real infrastructure.

## MVP experience

| Surface | CEO-facing behavior | Technical meaning |
|---|---|---|
| Portfolio summary | “3 deployments healthy, 1 needs attention, 1 not observed” | Aggregates fresh observations without hiding unknown/stale states |
| Deployment scope editor | On/off toggle for each known deployment | Writes only Hub Monitor configuration; never a deployment profile or orchestrator |
| Expected machine selector | Choose “Office Mac,” “Production server,” or another named machine | Stores a comparison expectation, not a scheduling or placement command |
| Actual topology | Shows which deployment and components were observed on which machine | Derived from read-only signed/attested observations |
| Expected-versus-observed view | Shows match, mismatch, missing, unexpected, stale, or unknown | Deterministic drift evaluation with source and timestamp |
| Release view | Plain-language current/outdated/unknown state with exact revision available on drill-down | Compares observed release to an approved expected release when supplied |
| Attention list | Explains what needs a technical person and why | Read-only findings with evidence references; no fix/apply button |

The default vocabulary is **Running as expected**, **Needs attention**, **Offline**,
**Not observed**, and **Observation stale**. Provider-specific identifiers and raw
metrics belong in drill-down, not the first screen.

## Read-only contract

Hub Monitor must not:

- provision, deploy, start, stop, restart, move, scale, upgrade, roll back, or delete
  a workload;
- run Terraform, Kubernetes apply, Docker mutation, cloud deployment APIs, or
  operator-update apply paths;
- modify `infra/deployment-profiles.yaml`, Terraform state, orchestrator desired
  state, machine services, firewall rules, DNS, certificates, or storage;
- write, display, rotate, or export raw secrets;
- represent “not observed” as healthy or infer health from an old snapshot; or
- offer a button whose wording implies that Hub Monitor will fix a finding.

Any future infrastructure management surface is a separate capability, contract,
threat model, and approval workflow. It cannot be introduced as an extension of
the Monitor MVP without an explicit claim change.

## Conceptual information model

The following is documentation vocabulary, not a machine schema in this pass.

| Object | Required meaning | Authority |
|---|---|---|
| Monitored deployment | Stable display identity, profile/release expectation, and `included` boolean | Hub Monitor configuration |
| Machine | Stable machine identity, human name, kind, and read-only observation route reference | Hub Monitor configuration plus verified machine evidence |
| Expected placement | Deployment-to-machine association used for comparison | Hub Monitor configuration only |
| Observation | Observed deployment, machine, release, component/health facts, source, observed time, expiry/freshness | Signed collector/provider result; cached by Monitor as derived data |
| Drift finding | Deterministic comparison between expected and observed facts | Hub Monitor evaluator |
| Evidence reference | Safe pointer to the source check, receipt, metric, or observation | Source system/evidence store |

The `included` boolean never means “deploy this.” `expected_machine_id` never means
“schedule this here.” Machine and deployment identities must remain stable across
display-name changes.

## Observation sources

MVP adapters may read:

- Hub deployment profiles for declared service membership;
- operator-state records for named node, source revision, images, and last apply;
- private service health/readiness endpoints;
- cloud or orchestrator inventory through read-only credentials;
- selected metrics/alarms and backup/recovery evidence;
- container, process, or machine-agent inventory; and
- secS receipts or attestations where they prove observation origin and freshness.

Every displayed fact must identify its source and observation time. Monitor must
distinguish source unavailability from an unhealthy deployment.

## Security and privacy

Hub Monitor is private and exposed only through the secS-magik admission layer,
whether secS is embedded in Hub or co-deployed. Its collectors use least-privilege,
read-only access. Collector write permissions are an MVP release blocker.

Monitor responses must redact secrets, tokens, raw environment values, private
network details that the viewer is not authorized to inspect, customer payloads,
and sensitive logs. The CEO summary can disclose operational state without making
raw health, metrics, cloud, or orchestrator endpoints public.

## Functional repository units

| FRU | Responsibility | MVP acceptance boundary |
|---|---|---|
| `FRU-MONITOR-001` Hub Monitor UI/API | Private CEO-facing inventory, topology, health, release, freshness, and drift experience | Accessible through secS; no infrastructure mutation operation exists |
| `FRU-MONITOR-CONTRACT-001` Monitor contract | Versioned monitoring-scope, machine, observation, freshness, and drift vocabulary | Boolean inclusion and expected placement are unambiguously observational |
| `FRU-MONITOR-COLLECTOR-001` Read-only collectors | Normalize profile, node, service, cloud/orchestrator, metric, and evidence observations | Read-only permissions and failure/expiry behavior are verified |

## Acceptance scenarios

1. A CEO opens Hub Monitor through secS and sees every included deployment grouped
   by named machine with plain-language status and freshness.
2. The CEO turns monitoring off for a lab deployment. It disappears from the
   portfolio calculation; the real lab deployment is unchanged.
3. The CEO maps “Production Hub” to “Production server.” Monitor later observes it
   on another machine and reports a placement mismatch without moving it.
4. A collector becomes unreachable. Monitor reports “Not observed” or “Observation
   stale,” not “Healthy.”
5. A technical user drills into a finding and sees exact revision, machine,
   component, source, timestamp, and safe evidence without receiving a mutation
   control or secret.
6. Automated tests prove that all Monitor routes and collector credentials are
   read-only and that no deploy/apply/restart path is reachable from the Monitor.

## Current implementation gap

| Existing substrate | Reusable evidence | Missing for Hub Monitor |
|---|---|---|
| Frank process dashboard | UI shell and case monitoring patterns | Infrastructure inventory, machine topology, freshness/drift model, CEO language |
| Deployment profiles | Declared profile/service membership | Stable deployment instances and expected-machine associations |
| Operator-state example/update planner | Node, source revision, images, plan/apply separation | Multi-node read model, observation ingestion, private API, UI; apply must stay disconnected |
| Health/smoke endpoints | Bounded point checks | Normalized observations, freshness, aggregation, source attribution |
| Matrix CloudWatch alarms | Selected production metrics and alarms | Cross-capability/profile coverage and read-only collector integration |

## Shared-contract boundary

This documentation pass changes no deployment profile, operator-state, API,
packet, observation, or persisted-data schema. A later implementation should add
new optional versioned Monitor contracts rather than reinterpret existing
`services.required`, `services.optional`, operator-state, smoke, or evidence fields.
Older profiles and operator-state records must continue to load unchanged.
