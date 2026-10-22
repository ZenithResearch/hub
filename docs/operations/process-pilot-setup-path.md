# Mac process pilot setup path

Hub should own the operator's setup experience: one release bundle, one per-host setup profile, lifecycle controls and a doctor report that identifies the component responsible for any blocker. The services can retain separate source repositories and private stores while being delivered as one local installation. The current Mac installer is the starting point; identity activation and request intake still require implementation, not just instructions.

## Start with one host

For the first shared pilot, run the stack on the operator's Mac and let both the operator and Jess submit requests through an existing Matrix homeserver. Once the intake capability is implemented, Matrix is the user-facing channel and the Service/Runtime APIs remain local to the host. This topology does not require exposing ports 8091 or 8092 to the internet.

Jess needs only a Matrix client to submit requests to that shared host. She needs the Mac installer and a separate native identity if she will host her own stack or execute work locally. Remote execution on her Mac additionally needs protected transport and explicit peer enrollment; installing the bundle alone does not supply that connection. Separate hosts do not share SQLite files or copy private identities.

The initial host contains Service Management, Process Runtime, Frank with intake and execution capabilities, native Wallet/secS helpers, and the private component stores. The provider and Matrix credentials are referenced by configuration. An existing Matrix homeserver supplies messaging. Devgraph and the roadmap remain separate work-tracking tools and are not prerequisites for storing a ServiceRequest.

## Reuse the lightweight pieces

| Existing surface | What it provides | Use for this pilot |
| --- | --- | --- |
| Devgraph standalone host, through `devgraph local ...` | A component-specific local runtime with persisted configuration, lifecycle, readiness, logs and recovery operations | Reuse its operational pattern; its CLI manages Devgraph and Neo4j, not arbitrary process components |
| Hub `scripts/process_mac.py` and `Process.command` | The direct Mac installer, pinned Python packages, private installation root, Service/Runtime lifecycle and passive Frank checks | Extend this existing controller into the pilot's single setup entry point |
| Hub `services/process_composition/` | Component/contract admission and combined operational checks | Keep deployment coordination and compatibility decisions here |
| Hub `apps/control-center/` on `codex/app-deployments-control-center` | A Tauri conversation and receipt-monitoring UI for App Deployments | Candidate operator UI after adding a supported local process profile; it currently targets deployment operations, not this pilot |
| Separate `zenith-hub` repository | A minimal Next.js canvas/sidebar frontend scaffold | Optional future web client; its scaffold has no connected process backend or authentication |

The Control Center worktree belongs to the same `ZenithResearch/hub` repository. It is not another independent Hub backend. The lightweight Devgraph host is its own service deployment; it is not a replacement for Service Management or Runtime.

## Source repository ownership

| Repository | Responsibility |
| --- | --- |
| `ZenithResearch/hub` | Installation, release pins, setup composition, process lifecycle, health and operator integration |
| Local `process-runtime` | Published processes, runs, dependency scheduling, Attempts, leases and committed outputs |
| Local `service-management` | Requests, qualification, acceptance, Cases, plans and closure |
| Local `frank` | Executor, provider adapters and delivery/reconciliation journal; proposed home for the conversational intake capability |
| `bananawalnut/castalia-wallet` | Native identity custody and holder signing |
| `ZenithResearch/secS-magik` | Native policy, context issuance and verification |
| `ZenithResearch/devgraph`, local `roadmap` | Implementation work/evidence and its roadmap view |

The active process work is in Hub's `codex/process-case-execution` worktree. As checked on 16 October 2026, the local Runtime, Service, Frank and Roadmap repositories have no Git remote configured. The Mac bundle exists locally; this does not establish a published GitHub release containing the completed native authority path. Repeatable source publication and distribution of the exact reviewed bundle belong in the setup delivery work.

Users should install that bundle, not clone all of these repositories. There is no need to merge them into a monorepo to provide one setup experience.

## Complete setup in useful increments

These are proposed operational milestones, not declarations that formal roadmap Gates have passed.

1. **Install the host.** Use the existing Mac bundle/controller. Package and pin the required native helpers with the compatible Service, Runtime and Frank artifacts. Keep the components' data ownership separate.
2. **Activate authorized local intake.** Finish and validate native bootstrap/startup wiring, approve the concrete live issuer role and pins, provision the selected host identities and load narrow receiver permissions. Prove an authorized Service `request.create` plus an authorized query; wrong authority must be rejected. The installed default-deny configuration does not yet satisfy this step.
3. **Connect the conversational inbox.** Add Frank's Service API client and Matrix intake capability. Configure the actual homeserver/account/room, supported room security, approved senders and secret references. Retain message-to-command correlation for exact retry; reply with a stored request ID only after the Service commit is confirmed. Matrix identity alone does not grant Service permissions.
4. **Enable one useful execution flow.** Publish the selected immutable process, configure outbound Service authority and handoff/progress workers, register Frank's scoped execution actor, and qualify one provider with native result authority. Intake permission does not automatically grant acceptance, plan approval or Case closure.
5. **Run the pilot and exercise recovery.** Submit a request, qualify/accept it, convert it to a Case, approve the exact process plan, execute it, inspect committed outputs and explicitly close the Case. Restart during an uncertain response and prove exact retry/reconciliation rather than a duplicate external effect.
6. **Add Jess's host only if needed.** Repeat the same installation with her own identity and private data, then enroll the required peer role/transport. If she is only a requester, continue using the shared Matrix front door.

The first useful acceptance check is “a permitted Matrix request receives a durable ServiceRequest ID and can be queried after restart.” It can precede live execution. The next check is “one accepted Case executes through an approved process and returns verified results.” These checkpoints make intake usability and execution readiness visible separately.

## Organize the product surfaces

| Audience | Entry point | Scope |
| --- | --- | --- |
| Host operator | Hub controller now; a guided Control Center profile later | Install, configure, enroll, enable, inspect, stop and recover |
| Requester | Frank conversation in Matrix after intake is wired | Submit, clarify and check a request; restricted decisions according to granted roles |
| Service owner | Proposed request/Case inbox, plus authorized API operations | Qualification, acceptance, plan decisions and closure |
| Application or automation | Service command/query API | Structured requests using the same native authority boundary |
| Developer/operator tracking work | Devgraph and roadmap | Implementation dependencies, evidence and delivery progress |

The operator should see one per-host setup profile covering identity references, roles, channel, provider, endpoints, data locations and release pins. The controller should derive and validate component configurations from it. Secret material remains in the appropriate custody/secret stores; the profile holds references. One operational view should explain whether intake, execution and provider connectivity are ready and give the next recovery action. That complete configuration/activation surface is still to be built.

See the [usage guide](process-pilot-usage-guide.md) for controls and the isolated request example available today, and [Mac installation](process-macos-installation.md) for the current installer contract.
