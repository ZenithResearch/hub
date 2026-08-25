# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

## [Unreleased]

### Fixed
- Bound custom Hypha broker image tags to a resolvable source commit with
  byte-identical Docker inputs, while recording both source and workflow
  revisions in OCI metadata.
- Bound Hypha broker publication and pulls to a dedicated immutable ECR
  repository in account `610992396917`, with exact GitHub production-environment
  OIDC trust, least-privilege publisher/instance permissions, fail-closed
  account validation, and ephemeral on-host Docker authentication.

### Added
- Added a fail-closed fresh Synapse backup and isolated-restore gate — creates
  hourly and daily application-consistent multi-volume DLM snapshots with
  PostgreSQL checkpoint/XFS freeze hooks, auto-thaw safety, bounded restore
  rehearsal cleanup, and mandatory recent recovery evidence before broker
  deployment.
- Added a scoped Hypha administration-session broker, hidden Synapse service-authority bootstrap, protected secret rotation, hardened image workflow, and rollback-capable SSM deployment path — lets the native client request short-lived typed administration authority without receiving registration credentials or a persistent Synapse administrator token.
- Added a fail-closed Matrix Authentication Service rollout contract — enables MSC4108 QR login without silently breaking existing users, sessions, devices, tokens, or E2EE state during the required `syn2mas` migration.
- Documented the digest-pinned Synapse landing rollout and issue boundary — keeps publication, Synapse-only deployment, API/federation smoke, browser QA, and rollback evidence explicit without claiming deployment.
- Added dynamic Synapse static-path installation and image-byte verification — avoids Python site-packages assumptions and proves the hardened image contains the reviewed landing-page bytes.
- Added the self-contained Zenith Synapse landing page and source contract tests — gives visitors a sparse, accessible route to ZenithOS without adding scripts, tracking, remote assets, or authority claims.
- Documented the Matrix administrator operator runbook — gives operators a secret-safe preflight, first-login password-change, Keychain cleanup, and account-verification procedure.
- Added controlled Matrix administrator provisioning — lets trusted operators create only explicit new Synapse admins while keeping generated credentials in Keychain and registration authority out of argv and output.
- Added Matrix admin provisioning contract tests — locks HMAC, temporary-password, fail-closed endpoint, safe-output, and post-registration Keychain behavior before implementation.
- Added EventBridge/Lambda automation for clients Postgres secret rotation — forces Gateway to restart after RDS-managed password rotation so ECS-injected database credentials do not go stale.
- Recorded ISS-P14-007 PR readiness evidence — preserves verification commands and the operator-auth limitation without overclaiming production apply.
- Added the Matrix production evidence runbook — gives operators exact redaction, plan/apply, smoke, and backup/restore steps for completing ISS-P14-007 safely.
- Hardened ISS-P14-007 evidence validation errors — reports compound smoke and P15-lock failures so operators can fix every edge case before claiming acceptance.
- Added the ISS-P14-007 evidence validator and template — gives operators a redacted, test-backed artifact contract before P15 can unlock.
- Added ISS-P14-007 production evidence contract tests — locks the required plan/apply, public smoke, backup/restore, sensitive-material, and P15-unlock edge cases before implementation.
- Added the ISS-P14-007 production evidence spec — defines the operator-gated plan/apply, smoke, backup/restore, and downstream-unlock evidence boundary before P15 proceeds.
- Added the Matrix CI Node 24 runtime hygiene issue spec — records this PR as CI substrate cleanup only, not P14 production deployment or smoke evidence.
- Recorded ISS-P14-005 PR readiness evidence — keeps backup verification commands and no-overclaim restore language visible for review.
- Added the Matrix AWS Backup contract and restore runbook — records the minimum state classes and unproven-restore boundary before claiming durable Synapse.
- Added ISS-P14-005 backup/restore contract tests — proves backup vault, schedule, retention, resource selection, and restore-runbook boundaries before implementation.
- Recorded ISS-P14-004 PR readiness evidence — verifies placeholder-only examples and non-leakage boundaries for Matrix secrets.
- Added Matrix Secrets Manager handles and secret-boundary runbook — keeps Synapse/appservice values out of committed files while preserving operator rotation ownership.
- Added ISS-P14-004 secret-boundary tests — proves Matrix secret classes are sensitive and Secrets Manager-backed before implementation.
- Recorded ISS-P14-003 PR readiness evidence — keeps DNS/TLS verification commands and forbidden production claims visible for review.
- Added the Matrix DNS/TLS Terraform contract and operator runbook — makes `synapse.zenith-research.ca` Route53/ACM/listener ownership explicit without claiming production readiness.
- Added ISS-P14-003 DNS/TLS contract tests — proves the Synapse public host, Route53, ACM, HTTPS, and 8448 federation contract before implementation.
- Added connected documentation maps and service-level READMEs — makes the Hub docs navigable from root README to service packages, operations runbooks, and focused verification commands.
- Added a production source ledger for main-head deployability — records live ECS image provenance, branch-lineage risks, feature-equivalence verification, and the remaining STT caveat so operators can plan a `main` roll-forward without losing currently deployed behavior.
- Added a standard clean-main production rollout runbook and README links — documents the preferred push → immutable image build → full Terraform plan/apply path so operators can redeploy without ad hoc partial updates.
- Added local STT configuration defaults to `.env.example` and README STT option guidance — makes local Whisper, ElevenLabs Scribe v2, fallback, and audio-preprocessor settings discoverable without reading Frank internals.
- Added `scripts/prod_build_image.sh` and routed the Gateway image workflow through it — gives local operators and future CD pipelines one reusable build/push path while keeping production rollout exclusively Terraform-controlled through `scripts/prod_terraform_cd.sh`.
- Added authenticated Gateway-owned HubFS `stat`, `content`, `list`, `manifest`, and `by-path` routes — gives ZenithOS direct POSIX-style access to the main Hub Gateway `/data` volume through the temporary admin-token bridge while keeping service-level filesystems as separate future volumes.
- Added Hub mirror file content serving for configured filesystem roots — lets ZenithOS fetch process documents and runtime `/data/...` files through authenticated Gateway admin routes when the operator has not mounted the backing Hub data directory.
- Added registered artifact content serving for case execution artifacts — lets ZenithOS preview Hub-resident Markdown/file slots through authenticated Gateway admin routes when the operator has not mounted the backing Hub data directory.
- Added review extraction page/scroll/stroke evidence capture — preserves navigation, scroll position, stroke event IDs, and deictic visual-artifact feedback in Frank review packets so implementation handoffs retain the UI context reviewers point at.
- Added Review Case Automaton operations documentation — records the finite state machine, public Gateway compatibility mapping, terminal failure semantics, and the decision to exclude Frank retry/rerun/fix-loop behavior from review or terminal states for now.
- Added Review Access policy allowlists — lets a project-scoped access code be constrained to explicit deployment/origin/subject combinations so Gallery/SWRL review access can stay Hub-owned without frontend secrets.
- Added Hub operator update boundary documentation and example operator-state manifest — clarifies that GitHub main is source of truth, not an automatic deployment trigger for any running node.
- Added `scripts/hub_update.py plan` with unit coverage — gives operators a no-side-effect way to compare target refs/profiles against local operator state before updating a Hub node.
- Added guarded `scripts/hub_update.py apply --dry-run` scaffolding — keeps apply explicit and prevents cloud-prod execution until Terraform backend access checks are implemented.

- Updated external-root docs/scripts for the post-reformat BJJ APFS layout: tooling caches now default to `/Volumes/BJJ-Cache/zenith-cache`, explicit runtime migrations use `/Volumes/BJJ-Runtime/zenith/data/cache`, and Docker Desktop's `Docker.raw` migration is recorded as external runtime state.

- Added a manual Gateway image build workflow and threaded `gateway_image_tag` through production CD so Project H Gateway endpoint code can be built in CI and rolled without local Docker builds.

- Added Gateway model-profile runtime persistence wiring — production task definitions now pass explicit model profile contract, override, and audit paths so ZenithOS/Hermes model-profile changes persist on gateway EFS instead of container-local state.

- Added audited model-profile binding updates — Gateway can now write safe runtime overrides, merge them into effective reads/checks, and append JSONL audit records with actor/time/config hashes/connectivity result while rejecting raw secret-looking updates.

- Added redacted model-profile connectivity checks — Gateway can now run a minimal OpenAI-compatible chat-completions probe for an effective agent/profile/deployment binding and return only safe operational status for ZenithOS.

- Added Hub-side model-profile resolution and a read-only admin endpoint for safe effective config display — ZenithOS can now query Frank's effective profile/model/endpoint/fallback metadata without raw secrets, while unknown profile bindings fail visibly instead of falling back to one global model.

- Added ZenithOS/Hermes-native model profile contracts and validation — records agent/persona → purpose profile → deployment profile bindings, the current Frank production Qwen llama-server default, safe secret-handle posture, fallback policy, and the future ZenithOS operator control-surface shape without introducing a single global model setting.

- Added configurable external-root contracts and Terraform wrapper scripts — keeps large local caches, temp files, model artifacts, and tool state off the Mac internal disk without duplicating providers per module or treating the repo as an artifact warehouse.

- Added deployment profile contracts and validation for local-dev, self-hosted-single-node, cloud-aws-staging, and cloud-aws-prod — makes data source-of-truth, Matrix/model posture, smoke commands, backup/reset policy, and CD authority explicit per environment.

- Added Matrix deployment parity docs and static checks — makes local Docker, self-hosted/cloud Matrix, appservice rendering, backup/restore, and non-health appservice smoke expectations explicit before any Matrix production deploy.

- Added a manual production CD baseline workflow, Terraform CD helper, and OIDC setup reference — enables approved OIDC-backed prod smoke/plan/apply runs with reviewed plan artifacts while keeping automatic deploys and long-lived AWS keys out of scope.

- Added a baseline GitHub Actions CI workflow and local `scripts/ci_check.sh` runner — keeps Python tests, private artifact scanning, Terraform fmt/validate, and Docker Compose config validation reproducible without requiring AWS production credentials.

- Fixed the llama model preload task image default to use the available AWS CLI public ECR `latest` tag after ECS could not pull `public.ecr.aws/aws-cli/aws-cli:2`.

- Added a reproducible llama-server model preload path: Terraform now defines a one-shot S3-to-EFS preload task and outputs, while `scripts/stage_llama_model.py` can upload a GGUF to private S3, run the preload task in private subnets, and verify the staged EFS artifact without local Docker builds.

- Codified the internal llama-server/Qwen ECS service for production drift adoption, including the private security group, Cloud Map entry, task role, log group, EFS read-only model mount, and import blocks for existing live resources.
- Split cases/Frank/STT image tag overrides so production plans do not regress service-specific hotfix images back to the gateway image tag.

### Changed
- Hardened the Hypha administration broker rollout with authority-aware readiness, single-flight service login, graceful HTTP transport shutdown, proxy body limits, transactional public smoke checks, and publish-only OIDC permission — prevents concurrent authority-token churn and keeps an unhealthy broker from being committed to the host.
- Added scope-checked private-artifact annotations for reviewed Python variable flows and test fixtures — preserves literal-secret detection while allowing the broker's credential plumbing to pass the same hosted CI gate as the rest of Hub.
- Synced ISS-P14-007 PR evidence back into the repo-local issue spec — makes the task-per-commit boundary and remaining operator-auth gate visible from the spec itself.
- Linked ISS-P14-007 from the Matrix issue index and P15 production homeserver spec — keeps downstream work blocked on accepted production evidence instead of older DNS/TLS shorthand.
- Upgraded the manual Gateway image workflow actions to Node 24-compatible releases — keeps the build-only image rail current without changing operator-controlled Terraform deployment ownership.
- Upgraded baseline CI workflow actions to Node 24-compatible releases — removes GitHub Actions runtime deprecation noise before the remaining Matrix production PR train.
- Split Gallery review access policy validation across separate apex and `www` production deployment rows while preserving `gallery-local` — lets Gallery support both production hostnames without dropping local Review SDK/admin access.
- Documented local Review SDK CORS origins in Gateway/Terraform operator docs — prevents production CORS allowlists from dropping localhost review/admin asset-upload origins during cleanup or Terraform refactors.

### Fixed
- Refreshed the Hypha administration broker to the digest-pinned Python 3.12.14 Bookworm base and split vulnerability reporting from the fixable-vulnerability gate — applies available Debian security updates while retaining visibility into upstream-unfixed findings.
- Added an exact service-start Terraform plan phase — permits only the reviewed MAS task-definition image replacements, MAS service update, and corresponding runtime alarm updates before authentication cutover.
- Preserved the signing-key secret bytes exactly when materializing the runtime PEM — avoids appending a second trailing newline to Secrets Manager values, which MAS rejects as an unsupported multi-document key format.
- Added migration-only Synapse PostgreSQL ingress from the MAS task security group — allows `syn2mas check` and migration rehearsals to reach the existing Synapse database without broadening steady-state access.
- Allowed only the required MAS task-definition replacement during the cutover phase — lets MAS transition from read-only migration mode to authoritative Synapse mode without weakening database or out-of-phase destruction guards.
- Unified MAS security-group rule ownership — MAS rules are standalone while existing Synapse/EFS groups retain conditional inline rules, preventing AWS provider refresh churn from deleting cutover or migration network paths.
- Added cutover-only bidirectional Synapse-to-MAS TCP/8080 rules and migration-only MAS-to-EFS TCP/2049 egress — delegated authentication and `syn2mas` can reach their private targets without broadening inactive-phase access.
- Closed final MAS rollout review blockers — exact phase/action plan policies now reject state destruction, phase one cannot publish the auth hostname, migration overrides match the wrapper contract, external DNS publication is explicit, and the exact ECR digest receives the zero-HIGH/CRITICAL scan.
- Made the pinned RDS CA bundle world-readable in the non-root MAS image — allows the runtime preflight and PostgreSQL `verify-full` connection to use it without relaxing process identity.
- Added a digest-pinned BusyBox shell/tool layer to the distroless MAS image — lets the secret-safe runtime wrapper execute without expanding to an unpinned general-purpose base image.
- Decoupled gateway Matrix environment injection from Synapse/MAS provisioning — prevents inactive authentication infrastructure plans from rolling the live gateway.
- Made the Docker-backed `make test` smoke use writable temporary review/client paths — keeps the import check reproducible when the image runs as the non-root app user and the copied `data/` tree is not writable.
- Added collectswirls origins to the Gateway CORS example — keeps SWRL Review SDK browser sessions from being blocked at preflight while the Hub Review Auth DB policy remains authoritative.
- Added regression coverage for Gallery review-access regeneration with compatibility deployment metadata — ensures renewing an existing project-scoped Gallery access code keeps the canonical apex, www, and local authentication model working without leaking raw secrets.
- Formatted the ISS-P14-005 Matrix backup Terraform contract — keeps the backup plan reviewable under the repo Terraform formatting gate.
- Corrected ISS-P14-005 readiness evidence to use the repo-local pytest invocation with an explicit pytest dependency — makes the recorded gate reproducible from a clean checkout.
- Corrected ISS-P14-004 readiness evidence to use the repo-local pytest invocation with an explicit pytest dependency — makes the recorded gate reproducible from a clean checkout.
- Corrected ISS-P14-003 readiness evidence to use the repo-local pytest invocation with an explicit pytest dependency — makes the recorded gate reproducible from a clean checkout.
- Fixed process-contract environment capability parsing — prevents inline defaults/examples like `none`, `elevenlabs_audio_isolation`, and `STT_PROVIDER=elevenlabs` from being reported as missing required runtime configuration.
- Made model-profile connectivity probes request a small multi-token completion — avoids a production llama.cpp prompt-cache edge case where one-token health checks could abort the internal Qwen server and surface as a 502 in ZenithOS.
- Returned explicit provider-secret write capabilities from Gateway runtime config status — lets ZenithOS distinguish unsupported Secrets Manager rotation from missing capability data.
- Hardened Frank native review-pipeline recovery for stale scheduled cases — retries IN_PROGRESS or BLOCKED native cases that still have runnable steps and no durable active runner so claimed review submissions cannot sit forever after an in-process scheduler task is lost.
- Hardened operator-controlled IaC planning — profile changes now keep profile-specific plan domains even when the source ref is unchanged, and the local production Terraform helper requires explicit live image tags instead of carrying stale hotfix defaults that could roll services backward.
- Mounted Frank execution artifacts read-only into the Gateway HubFS namespace — lets `/v1/admin/fs/by-path/...` resolve live `/data/frank_execution/...` review Markdown artifacts instead of 404ing on the Gateway-only EFS volume.
- Fixed Frank review status writeback so degraded packet statuses are relayed as review metadata instead of failing the case mechanically; only deterministic Step 8 errors now fail the step.
- Fixed Review Access rotation to replace stale policy rows before inserting the submitted policy set — prevents existing inactive Gallery policy rows from causing Postgres unique-constraint 500s during reviewer-key generation or repair.
- Consolidated Review Case Automaton repository docs — fixed the Mermaid diagram as the canonical operations contract, cross-linked Frank/Gateway docs, and marked the 2026-05-24 operator-update plan as historical.
- Updated Review Case Automaton metadata persistence — Gateway status writeback now persists packet/review outcome metadata without accepting Frank retry/rerun bookkeeping fields.
- Updated Hub operator update docs with the actual local production rollout state — distinguishes the successful local operator deploy from the intentionally removed GitHub Actions production CD path.
- Deployed the Review Access production image tag for the Hub ECS baseline — records the `review-access-20260516013607-1994d2d` image tag used for the live admin-rotation rollout so future Terraform plans do not drift back to the previous review-auth image.
- Documented the quick Matrix/Synapse-backed community setup path — shows the default feedback room bootstrap and how to create additional local Matrix rooms while warning that non-feedback bridge routing is still WIP.
- Rewrote the README around the current WIP trust boundary — tells new readers to expect breaking changes and to rely primarily on queue/cases orchestration plus Synapse while experimental surfaces keep moving.
- Clarified the Frank/Sophia runtime boundary and Frank model provider config — keeps Frank on the Hermes Codex provider path while Sophia remains a comms/profile surface rather than an execution profile.

### Removed
- Removed the GitHub Actions Production CD workflow — production deploys are intentionally local/operator-controlled, so leaving a known-nonfunctional CD workflow in Actions created misleading failure signals while CI and image-build workflows remain active.
- Removed Sophia-local case/step execution-loop skills — keeps Sophia strictly comms-only and prevents future dispatch paths from mistaking Sophia for an internal case executor.

### Added
- Added Terraform baseline resources for cases, eventbus, Frank, and STT HTTP — starts codifying the production native review service topology so ECS services, EFS mounts, IAM roles, security groups, service discovery, and task sizing can be reviewed instead of remaining hotfix-only drift.
- Added protected admin queue/case gateway read endpoints — lets ZenithOS operator surfaces read production queue and case state through Hub with the existing Review Access admin bearer-token boundary instead of direct internal service URLs.
- Added public-repo private artifact guardrails — staged/pre-push scans now block local runtime state, private ClaudeHub references, database files, local absolute paths, and likely secrets before they can enter or leave the public Hub repository.
- Added Postgres-backed Review SDK access management for production Hub — supports dynamic deploy registration, private RDS clients registry wiring, guarded operator access-code rotation for ZenithOS, one-time generated reviewer codes, project-scoped reviewer access, and redacted seeding/smoke-test tooling so public staging review auth can be managed without frontend secrets or local SQLite DB ferrying.
- Added authenticated Review SDK intake for public staging branches — Hub now owns a SQLite-backed review auth registry, short-lived review session tokens, project/deployment/origin-scoped validation, authenticated asset/review submission enforcement, attribution stamping, and regression tests so staging clients can submit reviews without bundling durable frontend secrets.
- Added Frank native case pipeline execution and cases observability APIs — `FRANK_RUNTIME` now defaults to `native_case_pipeline`, Frank can schedule/recover service-code case runs without Hermes Kanban child dispatch, and cases exposes run/step/span/event/artifact plus board projection endpoints so Swift ZenithOS can monitor execution from cases state.
- Added normalized Frank step I/O contracts for Kanban-projected case tasks — each task now carries a `StepIOContract` with named inputs, named outputs, and output schemas, while worker-facing task bodies describe the atomic `inputs/context -> metadata.zenith.outputs` contract and allow rich JSON or artifact-pointer values under declared output names.
- Added Frank review-packet hardening follow-up plan and acceptance harness — turns the Franklin26 inline production probe into a rerunnable local script and keeps post-signoff work bounded.
- Improved Frank review-packet source-binding ranking — implementation files now outrank stylesheet/docs matches and duplicate file references are collapsed before handoff.
- Added blocked-case operator follow-up endpoint — ZenithOS can attach human recovery input to BLOCKED cases and force-retry them without terminal intervention.
- Added review-packet handoff hardening implementation plan — preserves the reviewed work-surface index, scope gates, and deliverable contract used to make the Frank packet pipeline implementation-safe.
- Added ZenithOS blocked-case operator-input follow-up note — records the post-signoff monitor-screen recovery surface for cases that block and need human input.
- Added verified source binding for Frank review packets — known review subject URLs now map to mounted local codebases so review packets can reach `review_packet_ready` with concrete implementation files instead of deferred source-binding questions.
- Added Frank review packet agent handoff hardening — review packets now carry actionability triage, negative evidence, per-feedback source-binding state, and implementation handoff tasks so downstream agents receive a delegable payload instead of a markdown-only summary.
- Added Frank native review packet execution path — lets the live Frank case pipeline build grounded `review_packet.json` artifacts from review assets/events/transcripts and keeps mounted `/hub` source active in compose so verified code is what runs.
- Added local STT service isolation and Frank Kanban live-E2E blocker planning docs — review transcription now routes through a dedicated `stt-http` service/local Whisper tool contract, compose/quickstart cover the local ASR topology, and the parent-runner architecture repair is documented as planning without mixing the architectural rewrite into the blocker patch.
- Added Phase 12 default-runtime switch for Frank Kanban execution — production/default `FRANK_RUNTIME` now resolves to `kanban`, compose defaults to `${FRANK_RUNTIME:-kanban}`, explicit `FRANK_RUNTIME=direct` remains available as a fallback with a warning case log, and regression tests guard that default/kanban execution does not call the legacy step runner.
- Added Phase 11 Docker/E2E validation scaffolding for the Frank Kanban runtime — Frank keeps its identity `HERMES_HOME` while using `FRANK_KANBAN_HERMES_HOME=/hub/.hermes`, gateway and worker dispatcher services share the same Hermes home mount, the Phase 11 boundary kept `FRANK_RUNTIME` default-direct, the worker-queue runtime files are now tracked, and compose contract tests guard the shared-home/mock-E2E boundary.
- Added Phase 10 profile/process migration for the current nine-step queued-review process — process contracts now parse explicit `**Assignee:**` fields, both real and mock review processes keep `dispatch_profile`/executors on Frank while assigning worker-dispatchable Kanban tasks to the non-Sophia `worker` profile, Step 1 remains Frank-control/non-worker-dispatchable until deterministic setup completion, step briefs propagate assignees into Kanban projection, and runtime fixtures now distinguish Frank orchestration from Hermes Kanban assignee profiles.
- Added Phase 9 acceptance hardening for Frank Kanban runtime integration — cases service now exposes canonical model-task audit persistence with safe allowlisted pointer/hash storage, `HttpCaseRepository.upsert_model_task_audit()` writes through that endpoint, Kanban launch runs assignee/profile/model/skill/env/workspace preflight before materialization, and launch persists an explicit scheduler-handoff reconciliation trigger while keeping the dispatcher nudge global and direct runtime default unchanged.
- Added an initial Phase 9 Frank Kanban runtime integration path — `FRANK_RUNTIME=kanban` now materializes the compiled Kanban slice, optionally completes deterministic Step 1 through Kanban metadata, nudges the global Hermes dispatcher, and triggers reconciliation without changing the direct default or starting Phase 10 profile/process migration.
- Added the Phase 8 Frank Kanban reconciler without runtime wiring — polls mapped task/run state through the Kanban port, trusts only `metadata.zenith`, applies terminal run IDs exactly once, writes model-task audit references through the cases repository before state changes, validates declared outputs, and visibly blocks missing/unsafe metadata or failure outcomes without leaking secrets.
- Added the Phase 7 worker completion handoff contract to generated Frank Kanban task bodies — embeds canonical completion metadata JSON under `metadata.zenith`, declared output keys, neutral worker/CLI completion instructions, model-task audit pointer/hash requirements, and secret-redaction warnings while stopping before reconciler or runtime integration.
- Added the Phase 6 Frank Kanban slice materializer for the fake adapter only — materializes `CaseKanbanSliceSpec` tasks in topological order, passes parent task IDs during child creation, persists partial mappings after each successful create, resumes idempotently without duplicates, blocks visibly on stale/invalid existing task mappings, validates resumed parent IDs, and stops before real Hermes CLI runtime execution, reconciliation, or runtime wiring.
- Added the Phase 5 Frank Kanban slice compiler/projection migration — transforms the compiled case/process/dispatch contract into Phase 3 `CaseKanbanSliceSpec`, `KanbanTaskSpec`, and `KanbanLinkSpec` dataclasses, prefers resolved step briefs, validates assignees/workspaces/DAG edges, and stops before Hermes CLI calls, materialization, reconciliation, or runtime wiring.
- Added the Phase 4 Hermes CLI Kanban adapter — builds checked argv for real `hermes kanban` commands, parses Phase -1 JSON fixture shapes, overrides `HERMES_HOME` for Frank's Kanban home, filters secret-like env keys, redacts command failure text, and intentionally stops before materializer/reconciler/runtime integration.
- Added a queued-review codebase-context binding step before review document writing — gives product managers/planning agents implementation-aware references, likely causes, confidence, and caveats without turning the review into fix planning.
- Added the Phase 3B Frank case repository seam — defines a `CaseRepository` protocol and fake repository for dispatch packet merging, slot writes, step completion/runtime state, logs, and idempotent model-task audit references without adding materializer, reconciler, real Hermes adapter, or raw secret persistence.
- Added the Phase 3 Frank Kanban runtime port and fake adapter seam — defines stable Kanban task/link/slice dataclasses, a protocol, workspace/tenant validation, deterministic in-memory task IDs, and fake dependency-readiness dispatch so later materializer tests cannot dispatch children before parents complete; does not add real Hermes CLI execution, materialization, reconciliation, or SQLite access.
- Added `FRANK_RUNTIME=direct|kanban` as a Phase 2 runtime feature flag — keeps direct execution as the default while introducing a Kanban placeholder seam without creating Hermes tasks yet.
- Added Frank/Hermes runtime contract fixtures for the four-step review Kanban migration — freezes the case, dispatch, Kanban slice, run metadata, and Codex audit shapes before adapter/materializer/reconciler implementation.
- Moved canonical case execution ownership from Sophia to Frank and added the Frank-owned Hermes Kanban DAG projection path — aligns runtime ownership with the merged orchestrator-runtime architecture while keeping Sophia comms-only.
- `POST /v1/reviews` now enqueues a `review_submitted` message to the inbox queue service after writing the review record — fires to `QUEUE_HTTP_URL/queues/reviews/enqueue` with `event_type: "review_submitted"`, `source_type: "review_sdk"`, `sender`, `message_body: review_id`, and the full review record as `payload`. Degrades gracefully if the queue service is unreachable.
- `queue_http_url` config field on `GatewaySettings` (env `QUEUE_HTTP_URL`, default `http://localhost:8081`).
- `httpx>=0.27.0` dependency for async HTTP client used by the enqueue call.

### Fixed
- Fixed admin queue/case list payload bloat — queue peek and case lists now omit heavy payload/process fields by default while preserving explicit full-response query parameters for legacy callers.
- Fixed STT image dependency reproducibility — installs Whisper runtime dependencies explicitly before installing `openai-whisper` without dependency resolution churn.
- Fixed CI private-artifact scan range selection for first pushes to new branches — falls back to `origin/main...HEAD` when GitHub sends an all-zero `before` SHA so branch CI can validate deployment candidates.
- Fixed Review Case packet failure state handling — non-ready review packets now transition through a Cases-scoped automaton to terminal failed status with explicit metadata instead of remaining in processing with a failure reason, while ready packets preserve the existing processed compatibility status.
- Fixed browser-opaque Review SDK submission failures — `POST /v1/reviews` now converts queue enqueue `httpx` failures into CORS-visible `502` responses instead of leaking raw exceptions as Safari-reported CORS failures.
- Fixed Matrix appservice startup rendering — tracked Synapse templates now stay stable while generated appservice registrations render under `/data/appservices`, and startup only registers appservices whose receivers are launched by the same path.
- Hardened Hub-local Kanban worker prompt contract before fresh E2E — dispatcher-spawned workers are now explicitly told to call `kanban_show` first, report `KANBAN_TOOLS_MISSING` instead of narrating when tools are absent, avoid Docker/worker spawning, include `metadata.zenith.audit`, and write only declared `metadata.zenith.outputs` names.
- Fixed Hub-local Hermes Kanban dispatcher worker spawn context — spawned worker profiles now inherit the active `HERMES_KANBAN_BOARD` and an explicit `HERMES_HOME`, preserving case-board isolation without creating Docker-per-task environments.
- Fixed Hub-local Hermes Kanban JSON identity payloads — `create/show/list --json` now expose `idempotency_key`, allowing Frank reconciliation to validate board-scoped task identity instead of blocking every case step with `Kanban task identity mismatch: idempotency_key`.
- Fixed Frank and Hermes worker queue compose working directories — mounted `/hub` source now wins over stale image `/app` code during live E2E runs, so board/archive patches are actually exercised without a rebuild.
- Fixed review-packet acceptance script host path resolution — maps container `/hub/...` packet artifact paths back to the local repo so host-side acceptance can read emitted packets.
- Fixed Frank native review-packet output envelopes — Step 5 now returns the full packet-v2 contract, Step 6/7 no longer emit undeclared slots, and regression tests compare structured-analysis outputs against the compiled process contract.
- Fixed Frank Kanban live-E2E blocker paths around large outputs, reconciliation idempotency, and local transcription — deterministic Step 1 writes cases outputs before compact Kanban completion metadata, completion run IDs are recovered/stored after cases acceptance, artifact-only worker outputs block until hydration exists, live HTTP runtime-state shapes are respected, terminal done/completed worker runs reconcile, and model-backed audit step IDs are derived when workers omit them.
- Fixed the Phase 11 worker-launch logging blocker: Hermes worker queue launch metadata now redacts the `-q` prompt argv payload, drops `prompt_preview`, and persists only safe launch pointers plus prompt length/hash so cases logs do not store full worker prompts or model payloads.
- Fixed the Phase 8 reconciled-marker blocker: malformed or unresolved blocked runs (missing `metadata.zenith`, invalid/unsafe audits, output-schema mismatches, and audit upsert failures) now block visibly without setting `last_reconciled_run_id`; only successful application or valid recorded terminal failure/block outcomes advance the reconciled marker.
- Aligned the Phase 7 completed-run/audit fixtures with the current Step 3 contract — completed Step 3 now returns only `component_names`, audit workspaces match the referenced Kanban task workspace, and fixture tests reject run outputs outside declared step schemas.
- Hardened the Phase 6 materializer crash-before-persist window: after `create_task()` returns, reused/idempotent task IDs are now verified via `show_task()` for tenant, step identity, and expected parent IDs before the mapping is persisted.
- Hardened the Phase 4 Hermes CLI adapter tenant boundary: `create_task()` now rejects Hermes `create --json` payloads whose returned tenant differs from the requested case tenant, and tests no longer accept a `spike-case` fixture for a `case_123` task spec.
- Regenerated Frank runtime case, dispatch, Kanban slice, and partial-materialization fixtures from the current nine-step compiled queued-review process order — keeps `Resolve component names` at `step_3`, `Bind feedback to codebase context` at `step_6`, `Update review status` before `Log in daily note`, and fixture parent IDs aligned with the executable DAG.
- Reordered and specified the queued-review DAG/IO so component names resolve before transcript annotation/observation extraction, review documents include component-resolution/evidence sections, review-status writeback emits `review_status_updated`, and daily logging depends on that confirmation — prevents documents/logs from being written before component-linked observations and completed status updates exist.
- Corrected the queued-review process/runtime contract so compiled execution routes through Frank, not Sophia, and Step 1 no longer emits root context passthrough values that create self-DAG edges; runtime fixture tests now assert compiled/fixture execution profiles match and reject self-parent Kanban edges.
- Aligned Frank runtime fixtures with the current compiled review process and matched `CaseRepository.upsert_model_task_audit()` return shape to the Phase 1 audit fixture contract (`idempotency_key`, `upsert_status`, and normalized artifact pointer/hash keys).
- Bound the gateway HTTP compose port to localhost by default and kept admin-token/local-only protections — prevents unauthenticated admin config mutation from being remotely reachable in default Docker deployments.
- Normalized Frank workspace policy derivation to Hermes-valid workspace values (`scratch`, `worktree`, or `dir:<absolute-path>`) — prevents Phase 1 fixtures from freezing invalid `worktree:` or `scratch:` workspace strings.
- Hardened Frank-owned case execution contract after review: fixed zero-based Kanban DAG edge indexing, made unresolved DAG edges fail loudly, completed no-output steps through normal step status updates, derived/preserved workspace policy on step briefs, and made Frank avoid duplicate launches when durable active step runtime already exists.
- Removed Sophia from internal review execution process/test defaults; Frank is the narrow direct step-runner launcher for this slice while Sophia remains comms-only.
- Added missing `python-multipart>=0.0.9` dependency — required by FastAPI's `File`/`Form` multipart parsing used by `POST /v1/reviews/assets`; gateway crashed on startup without it.
- Matrix Synapse now renders and loads Sophia's resolved appservice registration, and the bot setup script targets the real `homeserver.yaml` — prevents unresolved appservice token templates from causing `401 Invalid access token` failures in the ZenithOS Synapse view.

### Added
- `reviews_data_dir` config field on `GatewaySettings` (env `REVIEWS_DATA_DIR`, default `data/reviews`) — allows hub-native review storage path to be overridden per environment.
- Path-based 50 MB body limit override in `BodySizeLimitMiddleware` for `POST /v1/reviews/assets` — asset uploads require a higher ceiling than the global 256 KB default while leaving all other routes unchanged.
- `POST /v1/reviews/assets` endpoint — accepts multipart binary uploads (events JSON, audio), assigns a UUID asset ID, writes raw bytes and a `.meta.json` sidecar to `data/reviews/assets/`.
- `POST /v1/reviews` endpoint — accepts a review record referencing previously uploaded asset IDs, validates all assets exist, writes `{review_id}.json` and returns `{ review_id, status: "queued", created_at }`.
- `GET /v1/reviews/{review_id}` endpoint — retrieves a stored review record by ID; returns 404 if not found.
- Pydantic models: `ReviewAssetUploadOut`, `ReviewSubmitIn`, `ReviewSubmitOut` (ZEN-88).

### Added
- `docs/gateway-http.md` — reference doc covering startup (full stack vs. standalone), all routes, env vars, middleware, and source layout.
