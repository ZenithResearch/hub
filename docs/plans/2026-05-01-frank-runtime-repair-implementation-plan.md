# Frank Hermes-native Kanban runtime implementation work order

This is the execution-ready work order for moving Frank from the current direct
step-runner runtime to a production Hermes-native Kanban runtime.

The architecture decision is fixed:

- Frank is the compiler and reconciler.
- Hermes Kanban is the operational execution substrate.
- Sophia is comms-only.
- Zenith cases remain the canonical product/control-plane record.

Do not implement seat isolation, secS federation, multi-machine tenancy, or a
custom dispatcher in this workstream.

References:

- Hermes Kanban overview: https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban
- Hermes Kanban tutorial: https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-tutorial

## Current baseline

Current repo state is not Kanban-native yet.

Existing relevant code:

- `services/frank/main.py` still executes cases through `start_case_execution()`,
  `launch_wave()`, `launch_step_runner()`, and `services.frank.step_runner`.
- `services/frank/kanban_projection.py` is a pure projection stub. It is not a
  live Hermes materializer.
- `build_dispatch_packet()` attaches `dispatch_packet["hermes_kanban"]`, but the
  data is only a plan/projection.
- `base/ops/processes/process-queued-review.md` currently has four review steps,
  not the older eight-step process.
- The current direct runtime must keep working until Kanban mode passes E2E.

Current review process steps:

```text
step_1 Load review record
step_2 Transcribe audio
step_3 Create review document
step_4 Update review status
```

Do not implement against stale eight-step assumptions unless a separate process
contract migration explicitly re-splits the process.

## Runtime invariants

Zenith owns:

- initiatives, projects, cases, steps, dependencies, slots, state, and logs
- canonical process contracts
- durable reconciliation state

Frank owns:

- queue intake
- process matching
- case creation/reuse
- root context writes
- process/case DAG compilation
- capability/config/profile preflight
- Hermes Kanban task-slice materialization
- global dispatcher nudge
- run reconciliation back into cases

Hermes Kanban owns:

- task rows in `kanban.db`
- parent/child dependency promotion
- task claiming
- worker spawning
- heartbeats
- retry/crash/run history
- task completion metadata and blocking status/events

Sophia owns:

- outbound communication
- summaries
- public/wiki/note synthesis when explicitly assigned
- no internal review execution by default

## Configuration invariants

Hermes Kanban home:

- Frank config variable: `FRANK_KANBAN_HERMES_HOME=/hub/.hermes`
- Hermes CLI subprocess env: `HERMES_HOME=$FRANK_KANBAN_HERMES_HOME`
- Hermes gateway/dispatcher env: `HERMES_HOME=/hub/.hermes`
- Tests must prove Kanban CLI calls do not use Frank's rolodex `HERMES_HOME`.

Runtime switch:

- Add `FRANK_RUNTIME=direct|kanban`.
- Initial default: `direct`.
- Production target default after E2E: `kanban`.
- `FRANK_DIRECT_RUN_FALLBACK=0|1` is only an emergency fallback after Kanban is
  the default.

Codex model config:

- Codex through Hermes means `model.provider: openai-codex`.
- Do not set `model.base_url`, `model.api_key`, auxiliary `base_url`, or
  auxiliary `api_key` when using `openai-codex`.
- Do not point Codex configs at `http://host.docker.internal:3690/v1`.
- Use `hermes model` to authenticate/select Codex for every active Hermes home.
- Containerized services must mount the Hermes home containing valid `auth.json`.

## Hard gates

Gate A: no Kanban adapter implementation until Phase -1 fixtures exist.

Gate B: no fake-adapter Kanban materialization until P0A-P0E pass.

Gate C: no Frank `kanban` runtime mode until compiler, fake adapter, and
materializer tests pass.

Gate D: no reconciler integration until `metadata.zenith` round trip is verified
through local Hermes `complete` and `runs --json`.

Gate E: no direct-run default removal until mock review E2E passes in Kanban
mode.

Gate F: no live Kanban E2E mode until P0F passes.

## Phase -1: Hermes Kanban CLI/schema spike

Purpose: freeze the real local Hermes CLI contract before implementation agents
write adapters.

Files:

- `tests/fixtures/hermes_kanban/help/*.txt`
- `tests/fixtures/hermes_kanban/*.json`
- `tests/fixtures/hermes_kanban/*.txt`

Commands to verify manually against an isolated Hermes home:

```bash
mkdir -p tests/fixtures/hermes_kanban/help
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban create --help > tests/fixtures/hermes_kanban/help/create.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban list --help > tests/fixtures/hermes_kanban/help/list.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban show --help > tests/fixtures/hermes_kanban/help/show.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban link --help > tests/fixtures/hermes_kanban/help/link.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban dispatch --help > tests/fixtures/hermes_kanban/help/dispatch.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban complete --help > tests/fixtures/hermes_kanban/help/complete.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban runs --help > tests/fixtures/hermes_kanban/help/runs.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban context --help > tests/fixtures/hermes_kanban/help/context.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban init --help > tests/fixtures/hermes_kanban/help/init.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban gc --help > tests/fixtures/hermes_kanban/help/gc.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban comment --help > tests/fixtures/hermes_kanban/help/comment.txt

HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban init
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban create "root" --assignee worker --tenant spike-case --workspace scratch --priority 5 --idempotency-key spike:root --json > tests/fixtures/hermes_kanban/create_root.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban create "root" --assignee worker --tenant spike-case --workspace scratch --priority 5 --idempotency-key spike:root --json > tests/fixtures/hermes_kanban/create_idempotency_reuse.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban create "child" --assignee worker --tenant spike-case --workspace scratch --parent <root_task_id> --idempotency-key spike:child --json > tests/fixtures/hermes_kanban/create_child_with_parent.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban list --tenant spike-case --json > tests/fixtures/hermes_kanban/list_tenant.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban show <task_id> --json > tests/fixtures/hermes_kanban/show_task.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban runs <root_task_id> --json > tests/fixtures/hermes_kanban/runs_empty.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban complete <root_task_id> --result completed --summary "done" --metadata '{"zenith":{"case_id":"spike-case","step_db_row_id":"step_db_1","step_id":"step_1","status":"completed","outputs":{},"notes":[],"artifacts":[]}}' > tests/fixtures/hermes_kanban/complete_success.stdout.txt 2> tests/fixtures/hermes_kanban/complete_success.stderr.txt
echo $? > tests/fixtures/hermes_kanban/complete_success.exitcode.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban runs <root_task_id> --json > tests/fixtures/hermes_kanban/runs_completed_metadata.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban show <root_task_id> --json > tests/fixtures/hermes_kanban/show_completed_task.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban create "repair child" --assignee worker --tenant spike-case --workspace scratch --idempotency-key spike:repair-child --json > tests/fixtures/hermes_kanban/create_repair_child.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban link <root_task_id> <repair_child_task_id> > tests/fixtures/hermes_kanban/link_success.stdout.txt 2> tests/fixtures/hermes_kanban/link_success.stderr.txt
echo $? > tests/fixtures/hermes_kanban/link_success.exitcode.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban link <root_task_id> <repair_child_task_id> > tests/fixtures/hermes_kanban/duplicate_link.stdout.txt 2> tests/fixtures/hermes_kanban/duplicate_link_error.txt
echo $? > tests/fixtures/hermes_kanban/duplicate_link.exitcode.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban create "blocked" --assignee worker --tenant spike-case --workspace scratch --idempotency-key spike:blocked --json > tests/fixtures/hermes_kanban/create_blocked.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban block <blocked_task_id> "needs input" > tests/fixtures/hermes_kanban/block_success.stdout.txt 2> tests/fixtures/hermes_kanban/block_success.stderr.txt
echo $? > tests/fixtures/hermes_kanban/block_success.exitcode.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban comment <blocked_task_id> '{"zenith_note":"blocked for spike validation"}' > tests/fixtures/hermes_kanban/comment_success.stdout.txt 2> tests/fixtures/hermes_kanban/comment_success.stderr.txt
echo $? > tests/fixtures/hermes_kanban/comment_success.exitcode.txt
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban show <blocked_task_id> --json > tests/fixtures/hermes_kanban/show_blocked_task.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban runs <blocked_task_id> --json > tests/fixtures/hermes_kanban/runs_blocked_task.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban dispatch --max 8 --json > tests/fixtures/hermes_kanban/dispatch.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban assignees --json > tests/fixtures/hermes_kanban/assignees.json
HERMES_HOME=/Users/bananawalnut/repos/hub/.tmp/hermes-kanban-spike hermes kanban gc
```

Required fixture captures:

- `create_root.json`
- `create_child_with_parent.json`
- `create_repair_child.json`
- `create_blocked.json`
- `create_idempotency_reuse.json`
- `list_tenant.json`
- `show_task.json`
- `show_completed_task.json`
- `show_blocked_task.json`
- `runs_empty.json`
- `runs_completed_metadata.json`
- `runs_blocked_task.json`
- `dispatch.json`
- `assignees.json`
- `complete_success.stdout.txt`
- `complete_success.stderr.txt`
- `complete_success.exitcode.txt`
- `block_success.stdout.txt`
- `block_success.stderr.txt`
- `block_success.exitcode.txt`
- `comment_success.stdout.txt`
- `comment_success.stderr.txt`
- `comment_success.exitcode.txt`
- `link_success.stdout.txt`
- `link_success.stderr.txt`
- `link_success.exitcode.txt`
- `duplicate_link.stdout.txt`
- `duplicate_link_error.txt`
- `duplicate_link.exitcode.txt`
- `help/create.txt`
- `help/list.txt`
- `help/show.txt`
- `help/link.txt`
- `help/dispatch.txt`
- `help/complete.txt`
- `help/runs.txt`
- `help/context.txt`
- `help/init.txt`
- `help/gc.txt`
- `help/comment.txt`

Acceptance:

- Confirm which commands support `--json`.
- Confirm `init`, `link`, `context`, and `gc` behavior without `--json`.
- Confirm `complete --metadata` appears in `runs --json`.
- Confirm `run_id` is available and stable enough for idempotent reconciliation.
- Confirm idempotency-key reuse behavior and returned shape.
- Confirm duplicate-link error shape if `link` is used for repair.
- Confirm whether `block` creates a run row or only changes task status/events.
- Confirm `comment` is human-readable only. If structured block metadata is
  needed, Frank stores it in cases logs; Kanban comments are operator/debug
  notes, not a reconciliation source of truth.
- Confirm the actual worker-side completion and parent-context mechanism; do not
  assume unverified callable names such as `kanban_complete()` or
  `kanban_show()`.

## Phase 0: prerequisite repair cards

Phase 0 must be split into separate cards. Do not combine these with Kanban
adapter work.

### P0A: root context slots and Step 1 output separation

Files:

- `services/frank/main.py`
- `tests/test_frank_dispatcher.py`
- `tests/test_frank_step_runner.py` if direct-mode behavior changes

Implementation:

- Add `write_root_context_slots(client, case_id, dispatch_packet, case_detail)`.
- Write root payload variables through `/cases/{case_id}/slots`.
- Required root slots for review:
  `review_id`, `audio_asset_id`, `events_asset_id`, `subject_id`,
  `submitted_by`, `reviewed_at`, `duration_ms`.
- Make writes idempotent: do not overwrite non-empty slots unless explicit policy
  says to do so.
- Deterministic Step 1 completion may only write declared Step 1 outputs:
  `review_id_short`, `audio_asset_path`, `events`.
- Remove or update tests that assert no `/slots` calls during dispatch.

Acceptance:

- Dispatch writes all root slots before step execution.
- Step 1 `complete_step_outputs` never receives undeclared root variables.
- Re-running dispatch against an existing case does not duplicate or corrupt slot
  values.

### P0B: capability/env collection and preflight

Files:

- `services/cases/contract.py`
- `services/frank/main.py`
- `docker-compose.yml`
- `tests/test_process_contract.py`
- `tests/test_frank_dispatcher.py`

Implementation:

- Parse process `### Environment` entries into the contract capability payload.
- Set `TOOL_DIR=/app/libs/tools` for services that collect capabilities:
  Frank and cases.
- Preserve tool-manifest-based env detection, but do not depend on `TOOL_DIR`
  alone.
- Frank preflight must block a case if required env vars are absent from both
  process env and configured secret file.

Acceptance:

- `dispatch_packet.capabilities.env_vars` includes `ELEVENLABS_API_KEY` for the
  review process without relying on ambient env.
- Missing `ELEVENLABS_API_KEY` marks the case blocked before Kanban/direct step
  execution starts.
- Existing direct runtime tests still pass.

### P0C: update_review_status tool env hardening

Files:

- `libs/tools/cases/tool.py`
- `libs/tools/cases/update_review_status/manifest.json`
- `docker-compose.yml`
- `tests/test_case_tools.py`

Implementation:

- Require `GATEWAY_HTTP_URL` for `update_review_status`.
- Add `GATEWAY_HTTP_URL` to `tool-sandbox` env.
- Remove the fallback that calls `/v1/reviews/...` through the cases client.
- Fail explicitly if `GATEWAY_HTTP_URL` is missing.

Acceptance:

- Tests assert failure when `GATEWAY_HTTP_URL` is absent.
- Tests assert the tool calls gateway, not cases, when updating review status.

### P0D: toolsets propagation

Files:

- `services/frank/main.py`
- `tests/test_frank_dispatcher.py`
- `tests/test_process_contract.py`

Implementation:

- Copy `contract_step["toolsets"]` into every step brief in `build_step_briefs()`.
- Ensure `build_allowed_toolsets()` sees parsed toolsets.
- Preserve existing resource/text heuristics as fallback only.

Acceptance:

- Review Step 3 `Suggested Toolsets: browser` reaches `allowed_toolsets`.
- Tests fail if parsed toolsets are dropped.

### P0E: gateway admin config protection

Files:

- `services/gateway_http/app.py`
- `services/gateway_http/static/dashboard.html`
- `tests/test_gateway_http_sessions.py` or a new gateway config test file
- `docker-compose.yml`

Implementation:

- Protect `/v1/admin/config*` endpoints with either local-only access or
  `GATEWAY_ADMIN_TOKEN`.
- Do not allow anonymous remote mutation of `ELEVENLABS_API_KEY`.
- Dashboard must send the admin token if token mode is enabled.

Acceptance:

- Unauthenticated secret read/write/delete requests are rejected.
- Authenticated or local-only requests still work in dev.
- Secrets are never echoed in full.

### P0F: Codex model configuration verification

Files:

- `docker-compose.yml`
- `rolodex/agents/frank/config.yaml`
- `.hermes/config.yaml`
- `.hermes/profiles/*/config.yaml`
- `.hermes/workers/*/config.yaml`
- `services/hermes_worker_queue/main.py`
- `tests/test_hermes_worker_queue.py`

Implementation:

- For `provider: openai-codex`, remove or ignore `base_url` and `api_key`; Codex
  auth must come from Hermes OAuth/auth state.
- For `provider: custom` or `provider: openai`, preserve explicit
  `base_url`/`api_key`.
- For `provider: openrouter`, do not strip credentials. Only remove a stale local
  Codex bridge endpoint value if it is specifically
  `http://host.docker.internal:3690/v1` or equivalent local `3690` endpoint.
- Do not treat `main` as a provider. If the Hermes config supports `main` as an
  auxiliary alias, preserve it as an alias and do not attach endpoint fields.
- Runtime-home materialization must not leak stale local `3690` endpoint fields
  into Codex profiles.

Acceptance:

- `rg 'host\.docker\.internal:3690|gpt-5\.3-codex-spark'` finds no active
  runtime config defaults.
- Focused worker queue tests verify stale endpoint removal.

## Phase 1: contract and fixture freeze

Purpose: give implementation agents fixed contracts before code generation.

Files:

- `tests/fixtures/frank_runtime/review_case_detail_4_step.json`
- `tests/fixtures/frank_runtime/review_dispatch_packet_direct.json`
- `tests/fixtures/frank_runtime/review_kanban_slice_spec.json`
- `tests/fixtures/frank_runtime/review_materialized_slice_partial.json`
- `tests/fixtures/frank_runtime/review_runs_completed_metadata.json`
- `tests/fixtures/frank_runtime/review_runs_missing_metadata.json`
- `tests/fixtures/frank_runtime/review_runs_failed_metadata.json`

Rules:

- Fixture process shape is the current four-step review process.
- Do not introduce the old eight-step process in these fixtures.
- `dispatch_profile: frank` means Frank compiles/materializes the task slice.
- Step `executor` means Hermes Kanban assignee.
- Sophia must not appear as an internal process executor.

### Codex Runtime Audit Contract

Frank/Hermes model execution must be auditable from Hub runtime artifacts,
not from the user's interactive Codex client.

For every model-backed task, persist:

- `case_id`
- `step_db_row_id`
- `kanban_task_id` once Kanban runtime exists
- `hermes_run_id`
- `profile`
- `provider`
- `model`
- `hermes_home`
- `workspace`
- prompt/task body snapshot, or content hash plus artifact path
- final response snapshot, or content hash plus artifact path
- tool calls requested/executed, with redacted env
- completion metadata written back to Zenith
- error/blocked outcome if applicable

Do not log auth tokens, OAuth payloads, API keys, raw secret env, passwords,
or connection strings.

Canonical audit records belong in the cases service / Zenith state, not only
sandbox-local files. Sandbox/session files can be supporting artifacts, but
the control plane must retain pointers and hashes.

Acceptance:

- Fixtures cover root-only, parent-child, output-producing, and no-output steps.
- Fixtures include at least one missing `metadata.zenith` run.
- Fixture IDs are stable and deterministic.
- Phase 1 fixtures include a representative canonical runtime audit record for
  a completed model-backed task.
- Phase 1 fixtures include a representative failed or blocked model-backed task
  audit record.
- Audit fixture schemas prove the control plane retains artifact pointers and
  hashes without persisting raw secrets.

## Phase 2: runtime feature flag

Files:

- `services/frank/main.py`
- `tests/test_frank_dispatcher.py`
- `docker-compose.yml`

Implementation:

- Add `FRANK_RUNTIME=direct|kanban`.
- Default to `direct`.
- Record runtime mode in dispatch packet and case dispatch logs.
- In `direct` mode, preserve current behavior after P0 fixes.
- In `kanban` mode, call a placeholder `launch_case_kanban_execution()` that can
  initially raise `NotImplementedError` behind tests.

Acceptance:

- Direct mode still launches current direct runner.
- Kanban mode does not call `launch_step_runner()`.
- Runtime mode appears in dispatch packet.

## Phase 3: Kanban runtime port and fake adapter

Files:

- `services/frank/kanban_client.py`
- `tests/test_frank_kanban_client.py`

Implement domain dataclasses:

```python
@dataclass(frozen=True)
class KanbanTaskSpec:
    case_id: str
    step_db_row_id: str
    step_id: str
    idx: int
    title: str
    body: str
    assignee: str
    workspace: str
    skills: tuple[str, ...]
    priority: int
    idempotency_key: str
    expected_outputs: dict[str, dict[str, Any]]

@dataclass(frozen=True)
class KanbanLinkSpec:
    parent_step_db_row_id: str
    child_step_db_row_id: str
    variables: tuple[str, ...]

@dataclass(frozen=True)
class CaseKanbanSliceSpec:
    case_id: str
    tenant: str
    process_path: str
    tasks: tuple[KanbanTaskSpec, ...]
    links: tuple[KanbanLinkSpec, ...]
```

Implement port:

```python
class KanbanRuntimePort(Protocol):
    async def init(self) -> None: ...
    async def create_task(self, spec: KanbanTaskSpec, *, parent_ids: list[str] | None = None) -> KanbanTaskRef: ...
    async def link(self, parent_id: str, child_id: str) -> None: ...
    async def complete(self, task_id: str, *, result: str, summary: str, metadata: dict[str, Any]) -> None: ...
    async def block(self, task_id: str, reason: str) -> None: ...
    async def dispatch(self, *, max_tasks: int | None = None) -> dict[str, Any]: ...
    async def list_tasks(self, *, tenant: str, include_archived: bool = False) -> list[dict[str, Any]]: ...
    async def show_task(self, task_id: str) -> dict[str, Any]: ...
    async def runs(self, task_id: str) -> list[dict[str, Any]]: ...
    async def assignees(self) -> list[str]: ...
```

Acceptance:

- Fake adapter supports deterministic task IDs.
- Fake adapter records parent IDs passed at task creation.
- No production code imports Hermes internals or SQLite.

## Phase 3B: case repository seam

Purpose: keep materializer/reconciler code out of `services/frank/main.py` and
make retry/idempotency behavior testable.

Files:

- `services/frank/case_repository.py`
- `tests/test_frank_case_repository.py`

Implement protocol:

```python
class CaseRepository(Protocol):
    async def get_case(self, case_id: str) -> dict[str, Any]: ...
    async def get_dispatch_packet(self, case_id: str) -> dict[str, Any]: ...
    async def merge_dispatch_packet(self, case_id: str, patch: dict[str, Any]) -> dict[str, Any]: ...
    async def write_slot_once(self, case_id: str, name: str, value: Any, *, agent_run_id: str | None = None) -> None: ...
    async def complete_step_outputs(self, case_id: str, step_id: str, outputs: dict[str, Any], *, agent_run_id: str, notes: list[str]) -> None: ...
    async def complete_no_output_step(self, case_id: str, step_id: str, *, agent_run_id: str, notes: list[str]) -> None: ...
    async def update_step_runtime_state(self, case_id: str, step_id: str, state: dict[str, Any]) -> None: ...
    async def upsert_model_task_audit(self, case_id: str, step_id: str, audit_record: dict[str, Any]) -> dict[str, Any]: ...
    async def append_log(self, case_id: str, log_type: str, message: str, *, metadata: dict[str, Any] | None = None) -> None: ...
```

Audit persistence rule:

- `upsert_model_task_audit()` writes the canonical Codex runtime audit record to
  the cases service / Zenith control-plane state and returns the audit reference
  it created or reused.
- The returned audit reference must be safe to place in dispatch packets, step
  runtime state, and case logs. It should include at minimum an audit record ID
  or stable key, the case/step/run identifiers, artifact paths, artifact hashes,
  and whether the upsert created or reused the record.
- The idempotency key is `hermes_run_id` when available; before a Hermes run
  exists, use a deterministic pre-run key derived from
  `case_id`, `step_db_row_id`, `profile`, and task artifact hash.
- The record may reference sandbox/session artifacts, but it must retain control
  plane pointers and hashes for the prompt/task body, final response, tool-call
  log, and completion metadata.
- The repository implementation must redact or reject auth tokens, OAuth
  payloads, API keys, raw secret env, passwords, and connection strings before
  writing the record.

Acceptance:

- Fake repository supports partial dispatch packet merge.
- Tests cover merge preservation of existing `hermes_kanban.materialization`.
- Materializer and reconciler depend on this protocol, not raw HTTP helpers in
  `main.py`.
- Fake repository supports idempotent model-task audit upsert by
  `hermes_run_id` and returns the same audit reference on replay.
- Tests prove audit upsert preserves artifact pointers/hashes and does not
  persist raw secret env or token-like fields.

## Phase 4: Hermes CLI adapter

Files:

- `services/frank/hermes_cli_kanban.py`
- `tests/test_frank_hermes_cli_kanban.py`
- `tests/fixtures/hermes_kanban/*`

Implementation:

- Build argv per command. Do not append `--json` generically.
- Use `--json` only for commands verified in Phase -1.
- Use checked non-JSON calls for `init`, `link`, `complete`, `block`,
  `heartbeat`, `archive`, and `gc` unless fixtures prove otherwise.
- Always invoke subprocesses with `HERMES_HOME=$FRANK_KANBAN_HERMES_HOME`.
- Never write `kanban.db` directly.

Required command builders:

- `init()`
- `create_task(spec, parent_ids=[...])`
- `list_tasks(tenant=...)`
- `show_task(task_id)`
- `runs(task_id)`
- `dispatch(max_tasks=8)`
- `assignees()`
- `complete(task_id, result, summary, metadata)`
- `block(task_id, reason)`
- `link(parent_id, child_id)` for repair only
- `gc()` for operations only

Acceptance:

- Tests assert exact argv for every command.
- Tests assert `create_task()` repeats `--parent` for multiple parents.
- Tests assert JSON parsing against Phase -1 fixtures.
- Tests assert non-JSON commands do not receive `--json`.

## Phase 5: Kanban slice compiler and projection migration

Files:

- `services/frank/kanban_projection.py`
- `services/frank/kanban_slice_compiler.py`
- `tests/test_frank_kanban_projection.py`
- `tests/test_frank_kanban_slice_compiler.py`

Decision:

- Either evolve `kanban_projection.py` into the target spec producer or make it a
  compatibility wrapper around `kanban_slice_compiler.py`.
- Do not create a second incompatible projection model.

Mapping from current projection:

- `CaseBoardPlan` becomes Zenith-side compatibility metadata only.
- Current planned `task_id` is not a Hermes task ID. Treat it as a planning key
  or replace with `idempotency_key`.
- `assigned_profile` maps to `assignee`.
- `workspace_policy` maps to Hermes `workspace`.
- `canonical_step_identity.step_db_row_id` maps to `step_db_row_id`.
- `expected_outputs` is preserved exactly.
- `resolved_step_brief` contributes task body, skills, tools, and output
  instructions.

Compiler rules:

- One executable case step maps to one Hermes task.
- Use `tenant=case_id`.
- Use stable idempotency key: `zenith:{case_id}:{step_db_row_id}`.
- Use zero-based DAG edges from the cases contract.
- Fail loudly on unresolved edges.
- Reject cyclic DAGs before materialization with a clear `ValueError` that names
  the involved step IDs.
- Validate workspace before task creation:
  `scratch`, `worktree`, or `dir:<absolute-path>`.
- Do not assign Sophia to internal tasks.
- Current first mapping targets the four-step review process.

Recommended temporary assignees:

```text
step_1 -> frank-control
step_2 -> worker
step_3 -> worker
step_4 -> frank-control
```

If `frank-control` does not exist yet, either create it in Phase 10 or assign all
steps to a non-Sophia `worker` profile until specialized profiles exist.

Acceptance:

- Generated spec matches `review_kanban_slice_spec.json`.
- Tests cover fan-out, pipeline, no-output steps, invalid workspaces, Sophia
  rejection, unresolved edges, and cyclic DAGs.

## Phase 6: materializer with fake adapter

Files:

- `services/frank/kanban_slice_materializer.py`
- `tests/test_frank_kanban_slice_materializer.py`
- `services/frank/main.py` only for repository helpers if needed

Implementation:

- Materialize tasks in topological order.
- Pass known parent Hermes task IDs to child `create_task(..., parent_ids=[...])`.
- Persist partial task mapping after each successful create.
- Re-fetch and merge existing `dispatch_packet["hermes_kanban"]` before writing.
- Do not overwrite an existing materialization mapping with a fresh empty packet.
- Treat stale/unknown Hermes task IDs as operator-visible block conditions, not
  permission to create duplicate tasks.
- Use `link()` only for repair of missing links, never normal creation.

Pseudo-flow:

```python
async def materialize(spec, case_id):
    packet = await cases.get_dispatch_packet(case_id)
    existing = MaterializedTaskSlice.from_packet(packet)
    task_ids = dict(existing.task_ids_by_step_db_row_id)

    for task in topological_order(spec.tasks, spec.links):
        if task.step_db_row_id in task_ids:
            await verify_task_exists(task_ids[task.step_db_row_id])
            continue

        parent_ids = [
            task_ids[link.parent_step_db_row_id]
            for link in spec.links
            if link.child_step_db_row_id == task.step_db_row_id
        ]
        ref = await kanban.create_task(task, parent_ids=parent_ids)
        task_ids[task.step_db_row_id] = ref.id
        await cases.merge_dispatch_packet(case_id, {"hermes_kanban": {"materialization": {"task_ids_by_step_db_row_id": task_ids}}})

    return MaterializedTaskSlice(...)
```

Acceptance:

- Crash after task 1 create resumes without duplicate task 1.
- Re-running materialization for the same case creates no duplicates.
- Child task creation includes parent IDs.
- Global dispatcher running during materialization cannot claim children before
  dependencies exist.

## Phase 7: worker completion handoff contract

Files:

- `services/frank/kanban_slice_compiler.py`
- `tests/test_frank_kanban_slice_compiler.py`
- `tests/fixtures/frank_runtime/review_runs_completed_metadata.json`
- worker skill docs if needed

Task body must include exact instructions:

```markdown
## Completion contract

Complete this Hermes Kanban task through the worker-side completion mechanism
verified in Phase -1.

Until Phase -1 proves a built-in worker callable name, write the task body using
neutral wording:

- If Hermes injects a documented worker tool/callable, use that exact interface.
- If completion must be performed by CLI, use:
  `hermes kanban complete <task_id> --result completed --summary <summary> --metadata <json>`.
- Do not invent tool names such as `kanban_complete()`.

The completion metadata must be JSON:

{
  "zenith": {
    "case_id": "{case_id}",
    "step_db_row_id": "{step_db_row_id}",
    "step_id": "{step_id}",
    "status": "completed",
    "outputs": {},
    "notes": [],
    "artifacts": [],
    "audit": {
      "case_id": "{case_id}",
      "step_db_row_id": "{step_db_row_id}",
      "kanban_task_id": "{task_id}",
      "hermes_run_id": "{hermes_run_id}",
      "profile": "{profile}",
      "provider": "{provider}",
      "model": "{model}",
      "hermes_home": "{hermes_home}",
      "workspace": "{workspace}",
      "prompt_artifact": "dir:/absolute/path/to/prompt.md",
      "prompt_sha256": "...",
      "final_response_artifact": "dir:/absolute/path/to/final.md",
      "final_response_sha256": "...",
      "tool_calls_artifact": "dir:/absolute/path/to/tool-calls.redacted.json",
      "tool_calls_sha256": "...",
      "completion_metadata_sha256": "...",
      "outcome": "completed"
    }
  }
}

If this step has declared outputs, `outputs` must contain exactly those keys.
If an output is too large, write it to an artifact and include:

{
  "zenith": {
    "outputs_artifact": "dir:/absolute/path/to/outputs.json",
    "outputs_sha256": "..."
  }
}

Do not mutate Zenith case state directly. Frank reconciles this run.

Audit handoff:

- For every model-backed task, the worker-side completion must include
  `metadata.zenith.audit` with pointers and hashes for the task prompt/body,
  final response, redacted tool-call log, and completion metadata.
- The worker may store supporting artifacts in sandbox/session files, but the
  completion metadata must carry enough pointers and hashes for Frank to write
  the canonical cases-service audit record during reconciliation.
- Tool-call artifacts must redact env values and must not contain auth tokens,
  OAuth payloads, API keys, raw secret env, passwords, or connection strings.
- Failed or blocked worker outcomes must still provide an audit record with
  `outcome` set to the terminal failure/block reason and final response fields
  omitted only when no response artifact exists.
```

Acceptance:

- Task body contains canonical IDs.
- Task body contains declared output schema.
- Task body instructs workers to inspect parent handoff using the verified
  Phase -1 interface. Candidate interfaces are Hermes worker-provided context,
  `hermes kanban show <task_id> --json`, or `hermes kanban context <task_id>`.
- Machine-readable dependencies must not rely on `context` unless Phase -1
  proves it has a machine-readable form.
- Phase -1 proves metadata survives into `runs --json`.
- A spawned worker can complete its own task with `metadata.zenith`.
- The completion and parent-context mechanism is documented in Phase -1 fixtures,
  local help output, or worker skill docs.
- Model-backed task bodies require workers to return `metadata.zenith.audit`
  artifact pointers/hashes for completed, failed, and blocked outcomes.
- Tests assert task bodies prohibit raw secrets in audit or tool-call artifacts.

## Phase 8: Kanban reconciler

Files:

- `services/frank/kanban_reconciler.py`
- `services/frank/main.py`
- `tests/test_frank_kanban_reconciler.py`

Implementation:

- Poll by tenant initially:
  `list_tasks(tenant=case_id)`, `show_task(task_id)`, `runs(task_id)`.
- For each mapped task, inspect both task state from `show_task()`/`list_tasks()`
  and latest terminal run from `runs()`.
- Parse only `run.metadata.zenith`.
- Ignore free-form result text as source of case outputs.
- Apply exactly once by `run_id`.
- Store `last_reconciled_run_id` in step runtime state or dispatch packet.
- Output-producing steps call `complete_step_outputs`.
- No-output steps call normal status update.
- Completion outputs come only from `run.metadata.zenith`.
- For model-backed tasks, extract `run.metadata.zenith.audit`, normalize it to
  the Phase 1 Codex Runtime Audit Contract, and call
  `CaseRepository.upsert_model_task_audit()` before applying step completion or
  failure state. Capture the returned audit reference and write it into step
  runtime state or dispatch packet before logging/applying the terminal state.
- If a terminal model-backed run has `metadata.zenith` but lacks a valid audit
  record, block the step visibly instead of silently completing it.
- Persist audit records for completed, failed, blocked, gave-up, timed-out,
  crashed, and spawn-failed outcomes. The audit record is written when the
  reconciler first observes the terminal run/outcome and is idempotent by
  `hermes_run_id`.
- Audit persistence must retain prompt/response/tool-call artifact pointers and
  hashes in cases-service state; sandbox-local files alone are insufficient.
- Redact or reject auth tokens, OAuth payloads, API keys, raw secret env,
  passwords, and connection strings before audit upsert.
- Blocked state may come from task status/events rather than run metadata,
  because local `hermes kanban block` does not expose structured metadata.
- Missing `metadata.zenith` blocks the step with a clear case log.
- Failure outcomes map through one strategy:
  `blocked`, `gave_up`, `timed_out`, `crashed`, `spawn_failed`.

Acceptance:

- Completed output-producing run writes validated outputs.
- Completed no-output run marks step completed without `complete-outputs`.
- Duplicate reconciliation of same `run_id` is a no-op.
- Missing metadata blocks visibly.
- Failed runs produce canonical case logs/state.
- Completed model-backed runs write canonical cases-service audit records before
  step completion is applied and retain the returned audit reference in runtime
  state.
- Failed or blocked model-backed runs write canonical cases-service audit records
  before failure/block state is applied when a run/audit payload exists, and
  retain the returned audit reference in runtime state.
- Missing or secret-bearing audit payloads for model-backed terminal runs block
  visibly and do not leak secrets into case logs.

## Phase 9: Frank kanban runtime integration

Files:

- `services/frank/main.py`
- `tests/test_frank_dispatcher.py`

Implementation:

- In `FRANK_RUNTIME=kanban`, `start_case_execution()` delegates to
  `launch_case_kanban_execution()`.
- `launch_case_kanban_execution()` sequence:
  1. Re-fetch case detail and dispatch packet.
  2. Write root context slots.
  3. Run config/capability/profile/workspace preflight.
  4. Compile task slice.
  5. Materialize task slice.
  6. If Step 1 is deterministic control-plane work, complete Step 1 through
     Hermes Kanban with `metadata.zenith`.
  7. For any model-backed control-plane task completed by Frank itself, write a
     canonical cases-service audit record through `CaseRepository` before
     applying step completion state, capture the returned audit reference, and
     persist that reference in dispatch packet or step runtime state.
  8. Nudge dispatcher with `dispatch(max_tasks=8)`.
  9. Log runtime mode, materialization IDs, and audit-record IDs/pointers in case
     logs without embedding prompt bodies, responses, or secrets.
  10. Start/trigger reconciler loop.
- Do not call `launch_step_runner()` in Kanban mode.

Runtime audit rule:

- Kanban integration is responsible for ensuring every model-backed task has a
  path to the canonical cases-service audit record: Frank writes it directly for
  Frank-owned model calls, and the reconciler writes it from
  `metadata.zenith.audit` for worker-owned Hermes runs.
- Dispatch success must not depend on sandbox-local audit files alone. The
  dispatch packet or case runtime state must retain audit record IDs/pointers
  and artifact hashes once they exist.
- Case logs may mention audit record IDs, artifact paths, and hashes, but must
  not include auth tokens, OAuth payloads, API keys, raw secret env, passwords,
  connection strings, full prompts, or full model responses.

Dispatcher nudge rule:

- `hermes kanban dispatch --max 8 --json` is global for the shared Kanban home.
- It is not tenant-scoped.
- Tests must not assume only this case's tasks are dispatched.
- Case isolation is through task `tenant`, task metadata, and reconciliation
  mapping, not through the dispatch command.

Acceptance:

- Direct mode still passes existing tests.
- Kanban mode never calls `launch_step_runner()`.
- Kanban mode persists materialization before acking successful dispatch.
- Runtime mode is visible in dispatch packet and case logs.
- Kanban mode has tested direct-Frank and worker-reconciled paths for writing
  canonical cases-service audit records.
- Runtime logs and dispatch packet contain audit pointers/hashes only, not raw
  secrets or full model payloads.

## Phase 10: profile and process migration

Files:

- `base/ops/processes/process-queued-review.md`
- `base/ops/processes/mock-review-submitted.md`
- `.hermes/profiles/*/config.yaml` if profiles are repo-managed
- `rolodex/index.yaml`
- tests

Rules:

- Keep `dispatch_profile: frank`.
- Step executors become Hermes Kanban assignees.
- Do not assign internal process execution to Sophia.
- Target the current four-step review process.
- Do not reintroduce stale eight-step mapping.

Initial production mapping:

```text
step_1 Load review record     -> frank-control or worker
step_2 Transcribe audio       -> worker
step_3 Create review document -> worker
step_4 Update review status   -> frank-control or worker
```

Acceptance:

- `hermes kanban assignees --json` shows every configured assignee.
- Every task-specific skill is installed for the assignee profile before any
  task is created.
- Sophia is absent from internal executor fields.

## Phase 11: Docker and E2E validation

Files:

- `docker-compose.yml`
- deployment scripts
- tests/e2e docs or scripts

Implementation:

- Add/confirm shared Kanban home mount for Frank and Hermes gateway/dispatcher.
- Add/confirm `FRANK_KANBAN_HERMES_HOME=/hub/.hermes` for Frank.
- Adapter subprocesses must set `HERMES_HOME` from
  `FRANK_KANBAN_HERMES_HOME`; they must not inherit Frank's identity/config
  home at `/hub/rolodex/agents/frank`.
- Frank's own `HERMES_HOME=/hub/rolodex/agents/frank` may remain for Frank
  identity/config.
- Add/confirm `TOOL_DIR=/app/libs/tools` for Frank and cases.
- Add/confirm `GATEWAY_HTTP_URL` for tool-sandbox.
- Confirm `HUB_CONFIG_SECRETS_PATH=/hub/.hermes/config-secrets.env` where needed.
- Start Hermes gateway/dispatcher against the same `HERMES_HOME=/hub/.hermes`.

Validation:

```bash
.venv/bin/python -m unittest \
  tests.test_frank_kanban_projection \
  tests.test_frank_kanban_client \
  tests.test_frank_hermes_cli_kanban \
  tests.test_frank_kanban_slice_compiler \
  tests.test_frank_kanban_slice_materializer \
  tests.test_frank_kanban_reconciler \
  tests.test_frank_case_repository \
  tests.test_frank_dispatcher \
  tests.test_process_contract \
  tests.test_case_tools
```

```bash
.venv/bin/python -m py_compile \
  services/frank/main.py \
  services/frank/kanban_projection.py \
  services/frank/kanban_client.py \
  services/frank/hermes_cli_kanban.py \
  services/frank/kanban_slice_compiler.py \
  services/frank/kanban_slice_materializer.py \
  services/frank/kanban_reconciler.py \
  services/frank/case_repository.py \
  services/cases/contract.py \
  services/gateway_http/app.py \
  libs/tools/cases/tool.py
```

```bash
docker compose config --quiet
```

Mock review E2E:

1. Run direct mode and verify no regression.
2. Run Kanban mode with shared Hermes home.
3. Submit `mock_review_submitted`.
4. Confirm Frank creates/reuses the case.
5. Confirm root slots are written.
6. Confirm Kanban tasks are materialized with tenant = case ID.
7. Confirm child tasks are not runnable before parents complete.
8. Complete or let Hermes run tasks.
9. Confirm `metadata.zenith` appears in `runs --json`.
10. Confirm Frank reconciles outputs into cases.
11. Confirm the case reaches terminal state.

## Phase 12: switch default and retire direct runner

Only start this phase after Gate E.

Files:

- `services/frank/main.py`
- `docker-compose.yml`
- tests

Implementation:

- Change production default to `FRANK_RUNTIME=kanban`.
- Keep direct mode behind explicit fallback only if still operationally useful.
- Log a warning whenever direct fallback is used.
- Add a regression test that fails if Kanban mode calls `launch_step_runner()`.

Acceptance:

- Production default uses Hermes Kanban dispatcher.
- Direct mode is disabled by default.
- Mock review E2E passes in Kanban mode.

## Subagent execution rules

Use one worker per card unless the write sets are disjoint.

Do not let workers edit `services/frank/main.py` concurrently unless their patch
targets have been explicitly separated.

Recommended parallelism:

- Phase -1 can run in parallel with P0E.
- P0B, P0C, and P0D can run in parallel if their tests are disjoint.
- Phase 3 fake adapter can start after Phase -1 schemas are captured.
- Phase 5 compiler and Phase 6 materializer should not run in parallel until
  `KanbanTaskSpec` is frozen.
- Phase 9 Frank integration must be single-owner.

## Final acceptance criteria

- `FRANK_RUNTIME=direct` still works until the final default switch.
- `FRANK_RUNTIME=kanban` never launches `services.frank.step_runner`.
- Frank writes root slots before execution.
- Hermes Kanban tasks are tenant-scoped by case ID.
- Children are created with parent IDs at task creation time.
- Dispatcher nudge is treated as global.
- Worker completion uses `metadata.zenith`.
- Reconciliation is idempotent by Hermes `run_id`.
- Output schemas are enforced.
- Missing metadata blocks visibly.
- Sophia is not an internal executor.
- Codex configs use Hermes `openai-codex` auth, not the local `3690` endpoint.
