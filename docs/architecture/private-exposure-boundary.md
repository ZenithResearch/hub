# Hub Private Exposure Boundary

Hub's target architecture is private by construction. Every Hub-owned runtime,
state service, adapter, and operator API must be reachable only on a host-local,
overlay, cluster-private, or VPC-private network. The only external admission path
is a secS-magik verifier/permissioned-RPC boundary.

This is an approved target contract, not a claim that current profiles enforce it.
Current Gateway HTTP, ALB/CloudFront, on-premises ingress, and Matrix client or
federation paths can bypass secS-magik and therefore do not conform yet.

## Ownership boundary

| Owner | Owns | Does not own |
|---|---|---|
| secS-magik | Final admission and authorization of every external call; envelope, signature, evidence, capability, replay, expiry, operation-descriptor, and verified-context checks | Hub business state, DevGraph objects, Matrix state, Queue state, model execution, or object bytes |
| Hub | Private component networking, receiver-local Hub operation descriptors/handlers, state mutation after verified admission, deployment composition, and operating evidence | External identity issuance, raw wallet/browser login, or a second independent public auth gate |
| Identity/evidence providers | Credentials, presentations, or evidence consumed by secS adapters | Direct Hub access or Hub operation authorization |

“secS-magik owns the auth gate” means that secS makes the final allow/deny decision
before a call enters Hub. Browser or wallet login may produce identity evidence, but
Hub must not accept that evidence directly as authority and secS-magik does not need
to become the identity issuer.

## Target topology

```text
external caller / agent / app
             |
             v
  secS-magik verifier + permissioned RPC
             |
             | verified operation context only
             v
  private Hub receiver / protocol adapter
             |
             +--> DevGraph
             +--> Matrix
             +--> Queue
             +--> Inference server
             +--> Object storage

  All Hub components, databases, files, admin APIs, and service ports remain private.
```

The semantic operations may use names such as `devgraph.*`, `matrix.*`, `queue.*`,
`inference.invoke`, and `object.*`. Exact `u8` opcodes remain receiver-local and are
not assigned by this document. A future Hub receiver manifest must bind semantic
operations to local handlers and required secS evidence/capability policy.

## Mandatory invariants

1. No external route reaches Gateway, Synapse, Queue, inference, object storage,
   DevGraph, databases, or operator endpoints without a secS verification decision.
2. Hub services accept only private-network calls from named service identities or a
   secS-verified receiver adapter.
3. Hub does not maintain a second public authorization truth. Existing Review Auth
   can remain as workflow/session data during migration but cannot be the external
   admission authority in the target architecture.
4. secS passes a signed or otherwise cryptographically bound verified-call context;
   Hub does not trust caller-supplied identity, role, capability, or evidence headers.
5. Rejection happens before Queue writes, DevGraph mutations, Matrix operations,
   inference execution, object writes, or any other material side effect.
6. Matrix client and federation exposure, if supported, must terminate or proxy
   through a secS-owned ingress path. Direct public Synapse routes are incompatible.
7. Administrative and health surfaces are private. External health disclosure, if
   needed, is a redacted secS operation rather than a raw service endpoint.
8. The same boundary applies in cloud, local development, self-hosted, and on-premises
   modes. “Virtually private” includes loopback, Unix sockets, private container
   networks, overlay networks, Kubernetes `ClusterIP`, and private VPC subnets.

## Minimal product capability surface

Only these Hub outcomes are top-level claims:

| Capability | Owner/substrate | Target external operation |
|---|---|---|
| DevGraph work graph | DevGraph service, Hub private adapter | Query and mutate authorized graph-shaped work state |
| Matrix messaging | Synapse/Matrix adapters | Exchange authorized messages and Matrix operations |
| Durable Queue | Hub Queue | Enqueue, claim, settle, retry, and inspect authorized work |
| Inference server | Private model server | Execute an authorized inference request |
| Object storage | Private object authority | Put, get, list, version, and lifecycle authorized objects |
| Private deployment boundary | Hub infrastructure plus secS-magik | Make every capability reachable only through verified secS operations |
| Operability | Hub operator controls | Deploy, verify, back up, restore, update, and observe the private composition |

Gateway, Cases, Frank, Runtime, Sandbox, STT, indexers, agents, tools, model profiles,
and other repository units are internal implementation components. Their existence
does not create separate external product claims.

## Current implementation gap

| Requirement | Current evidence | Status |
|---|---|---|
| Hub secS receiver/adapter | Root README previously asserted integration, but no Hub secS adapter, verified-context decoder, receiver manifest, or integration test exists | Not implemented |
| secS-only ingress | Current AWS Gateway ALB/CloudFront, Matrix public routes, standalone Matrix EIP, and on-premises ingress bypass secS | Not implemented; current profiles are non-conformant |
| DevGraph integration | DevGraph has repository-verified storage/model/auth/API/client layers in its own repo, but no Hub client, service membership, or secS operation binding exists | External substrate exists; Hub integration not implemented |
| Matrix substrate | Local and AWS Matrix profiles, adapters, recovery controls, and historical operating evidence exist | Implemented substrate; secS-gated composition absent |
| Queue substrate | Durable single-writer Queue HTTP/gRPC and deployment wiring exist | Implemented substrate; secS-gated operation absent |
| Inference substrate | AWS baseline provides a private llama-compatible server and model preload path | Implemented substrate; secS operation binding and current live evidence absent |
| General object storage | S3 is used for model artifacts/Terraform state and EFS/files for runtime artifacts | Not a general object authority; product capability not implemented |
| Private operability | IaC, validators, rollout, Matrix recovery, and tests exist | Partial; no complete private secS composition, general recovery, or conformance evidence |

## Required implementation sequence

1. Define the Hub receiver manifest and semantic operation catalogue without assigning
   global opcodes or changing `ZenithPacket` v0.
2. Define and verify the secS-to-Hub `VerifiedCallContext` handoff, including audience,
   operation, resource, subject, expiry, replay scope, and receipt correlation.
3. Add private adapters for DevGraph, Matrix, Queue, inference, and object storage.
4. Introduce the general object authority and migrate artifact references without
   breaking existing Cases, Review, HubFS, or stored packet readers.
5. Convert Gateway into a private aggregation/compatibility component and remove its
   public-auth ownership after all consumers use secS admission.
6. Replace public ALB/Synapse/on-premises paths with secS-only ingress and fail-closed
   network controls.
7. Add end-to-end rejection-before-side-effect tests and private-composition evidence.

No machine profile, persisted packet, API schema, or secS opcode changes in this
documentation pass. Those are shared-contract changes and require producer,
consumer, stored-shape, migration, and compatibility verification before adoption.
