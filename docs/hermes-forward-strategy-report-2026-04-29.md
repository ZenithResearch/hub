# Hermes-Forward Strategy Report

Date: 2026-04-29

## Scope

This report scans the last five daily notes and extracts the decisions that matter
for the hub's Hermes-forward case-dispatch architecture.

Notes reviewed:
- ClaudeHub daily note `notes/2026-04-25.md`
- ClaudeHub daily note `notes/2026-04-26.md`
- ClaudeHub daily note `notes/2026-04-27.md`
- ClaudeHub daily note `notes/2026-04-28.md`
- ClaudeHub daily note `notes/2026-04-29.md`

Actionable Hermes-direction signal was concentrated on 2026-04-27 through 2026-04-29, with the densest source-backed guidance on 2026-04-28.

## Executive Read

The recent notes support a clear strategy:

- The hub should become more Hermes-forward.
- Frank should remain the durable case dispatcher and policy boundary.
- Worker execution should happen by invoking Hermes profiles natively on the Docker backend.
- The launch boundary should be event-driven, not SSH-driven.
- Hermes persona state should stay isolated.
- Shared Hermes state should be treated carefully because the Hermes docs imply a single-writer durability model.

The most important constraint from the notes is this:

Hermes profiles and Hermes Docker are good at state isolation and backend execution, but they are not themselves a durable workflow engine. The hub still needs to own case state, dispatch policy, and event semantics.

## What The Notes Say

### 1. Hermes rollout should stay local-first and layered

From 2026-04-28:
- one clean local chat and session resume should be proven before adding gateways, cron, skills, or routing
- Hermes should be adopted in layers, not all at once

Architectural implication:
- The right next step is not "push more logic into Frank prompts"
- The right next step is "make one clean worker-launch path from `case.dispatch.requested` into a Docker-backend Hermes run"

### 2. Profiles are state boundaries, not sandboxes

From 2026-04-28:
- profiles isolate `config.yaml`, `.env`, memory, sessions, skills, and gateway state
- profiles do not sandbox filesystem access
- `terminal.cwd` is a separate control layer

Architectural implication:
- persona identity should map to isolated Hermes homes
- the hub should not confuse profile identity with execution sandboxing
- worker safety still depends on the Docker backend, tool boundaries, and runtime mounts

### 3. Docker deployment and Docker terminal backend are different things

From 2026-04-28:
- Hermes-in-Docker and Docker-as-terminal-backend are distinct patterns
- the durable Hermes data directory is effectively a single-writer state root
- concurrent writers against one shared state root are unsafe

Architectural implication:
- if we launch Hermes workers on the Docker backend, we should not let multiple independent launcher paths write to the same mutable Hermes state root casually
- if multiple personas exist, they should either:
  - have isolated `HERMES_HOME`s, or
  - be coordinated through a single launcher that owns state transitions

This strongly supports the isolated-worker direction we already started.

### 4. Security shifts from approval to structure as backends harden

From 2026-04-28:
- dangerous-command approval is mainly a host fallback
- sandbox backends depend on explicit credential filtering, context scanning, per-user isolation, and validated paths

Architectural implication:
- once the worker is launched in Docker, "Hermes asked nicely" is not the control plane
- the control plane should be:
  - explicit dispatch event
  - explicit persona selection
  - explicit mounted runtime/state root
  - explicit case tool access

This argues against reintroducing ad hoc shell dispatch or prompt improvisation.

### 5. Space-local scout agents are compatible with the hub, but they should emit structured triggers

From 2026-04-28:
- a scout agent should sit inside each space, mine signals, and emit structured triggers

Architectural implication:
- scouts should publish events into the hub
- Frank should convert those into durable cases when SOP-style execution is warranted
- the queue remains the narrow intake path for durable event-driven work

This supports the current event model rather than weakening it.

### 6. secZ was already framed as the functional dispatcher and Hermes bridge

From 2026-04-27:
- `secZ` includes "Build the Functional Dispatcher & Hermes Bridge"

From 2026-04-29:
- secS/secZ daemon architecture work progressed locally

Architectural implication:
- the present hub changes should be treated as the temporary in-hub version of that bridge
- we should avoid baking too much permanent worker-launch policy into Frank if secZ is meant to absorb that bridge role soon

That means the near-term design should be minimal and swappable.

## Recommended Strategy

### Near-term

Use this shape now:

1. `gateway_http` or `ingest` emits queue trigger.
2. Frank dequeues, resolves process, creates or reuses case, stores dispatch packet.
3. Frank publishes `case.dispatch.requested`.
4. A Docker-backend Hermes bridge consumes that event.
5. That bridge invokes Hermes using native profile selection:
   - requested profile if Frank specified one
   - default profile if none was specified
6. The Hermes worker reads `case_id`, reads `dispatch_packet_json`, writes initial root slots, and starts the case loop.

This fits both the recent notes and the current code trajectory.

### Medium-term

Move the bridge responsibility toward secZ.

The notes suggest secZ should become:
- the functional dispatcher
- the Hermes bridge
- the policy enforcement layer

That means Frank should probably stabilize as:
- queue intake
- sender/trust/process resolution
- durable case creation
- dispatch intent publication

And secZ should eventually own:
- case-dispatch event consumption
- Hermes profile/runtime selection
- Docker-backend Hermes invocation
- subprocess/process-group safety
- policy enforcement around what may run where

### What not to do

The notes argue against these moves:

- Do not return to SSH launch as the main path.
- Do not rely on one shared mutable Hermes state root with many competing writers.
- Do not treat profiles as sandboxes.
- Do not let prompt logic decide durable transport behavior.
- Do not assume gateway-style multi-persona runtime is safe without token and state partitioning.
- Do not build a parallel persona system if Hermes profiles already supply the identity/state boundary you need.

## Concrete Design Guidance For The Next Step

For the upcoming worker consumer, the clean design is:

### Event

Consume:
- `case.dispatch.requested`

Payload should remain minimal:
- `case_id`
- `queue_message_id`
- optional `executor`
- `hermes_home`
- `mode=docker_backend`
- `event_type`

### Consumer

Implement one bridge service that:
- subscribes to `case.dispatch.requested`
- decides the Hermes profile target
- invokes Hermes on the Docker backend under that profile
- records launch acceptance/failure back to the case

### Runtime selection rule

- If Frank selected an executor, map it to a Hermes profile and run Hermes with that profile
- If Frank selected none, run Hermes under the default profile

This should use Hermes-native profile semantics, not an app-specific imitation of them. The Hermes docs are explicit that:
- a profile is a separate Hermes home directory
- every profile already gets its own command alias
- `hermes -p <name> ...` explicitly targets a profile
- aliases are thin wrappers over `HERMES_HOME`

So the bridge should provision real Hermes profiles and invoke them natively rather than inventing a separate launcher identity model.

### State rule

- Treat each persona home as its own mutable state boundary
- Avoid concurrent writers against the same home unless one launcher explicitly owns that coordination

### Case rule

- The worker is still case-driven
- The worker should not invent process structure
- The worker should write initial payload-derived slots from the dispatch packet

## Final Recommendation

The right Hermes-forward strategy is:

- keep the hub as the durable event/case system
- keep Frank as the deterministic dispatcher
- use Hermes as the worker runtime identity and execution substrate
- provision worker personas as native Hermes profiles
- bridge into Hermes through event-driven Docker-backend profile invocation
- keep persona state isolated
- treat secZ as the likely long-term home of the launcher/policy bridge

So the next implementation should not be "make Frank smarter."

It should be:

"Add the single consumer for `case.dispatch.requested`, invoke Hermes natively with the selected profile or default profile on the Docker backend, and keep that bridge minimal so secZ can absorb it cleanly later."
