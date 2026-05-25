# Frank Native Case Pipeline

Frank's native case pipeline is the replacement execution path for review cases.
It is service code in `services/frank`, not a prompted Hermes child session and
not Hermes Kanban task dispatch.

## Runtime

- Default runtime: `native_case_pipeline` (service-code default; compose does not wire `FRANK_RUNTIME`)
- Unsupported runtimes: `direct` and `kanban` are obsolete and rejected
- Execution source of truth: cases / Zenith state
- Board source of truth: cases-derived run and step state

Frank writes case outputs before it advances any projected run, step, or board
state. This preserves the data-plane invariant that UI/checklist state never
claims a step is complete before cases accepts the outputs.

## Observability Model

The cases service exposes first-class observability records:

- `case_runs`: one execution attempt for a case.
- `step_runs`: one execution attempt for a process step.
- `execution_spans`: nested task or tool scopes inside a step.
- `execution_events`: append-only timeline records.
- `execution_artifacts`: safe artifact references with hashes, sizes, content
  type, and redaction status.

Existing case logs remain compatibility summaries. Swift ZenithOS should prefer
the run, step, span, event, and artifact APIs for live monitoring and drill-down.

## API Contract

- `POST /cases/{case_id}/runs`
- `GET /cases/{case_id}/runs`
- `GET /case-runs/{run_id}`
- `PUT /case-runs/{run_id}`
- `POST /case-runs/{run_id}/steps`
- `GET /case-runs/{run_id}/steps`
- `PUT /step-runs/{step_run_id}`
- `POST /case-runs/{run_id}/spans`
- `GET /step-runs/{step_run_id}/spans`
- `PUT /execution-spans/{span_id}`
- `POST /case-runs/{run_id}/events`
- `GET /case-runs/{run_id}/events`
- `GET /step-runs/{step_run_id}/events`
- `POST /case-runs/{run_id}/artifacts`
- `GET /case-runs/{run_id}/artifacts`
- `GET /step-runs/{step_run_id}/artifacts`
- `GET /case-runs/{run_id}/stream`
- `GET /cases/{case_id}/board`

`GET /cases/{case_id}/stream` continues to emit compatibility case updates and
now also receives run, step-run, span, event, and artifact update notifications.

## Statuses

Case run statuses:

- `queued`
- `running`
- `completed`
- `blocked`
- `failed`
- `cancelled`

Step run statuses:

- `pending`
- `ready`
- `running`
- `completed`
- `blocked`
- `failed`
- `skipped`

Span statuses:

- `running`
- `completed`
- `blocked`
- `failed`

## Swift ZenithOS Consumption

The Swift UI should:

- Load `GET /cases/{case_id}/runs` and select the active or latest run.
- Load `GET /case-runs/{run_id}/steps` for the step list.
- Load `GET /step-runs/{step_run_id}/events` for step-specific timelines.
- Load `GET /step-runs/{step_run_id}/spans` for nested tool/section scopes.
- Load `GET /step-runs/{step_run_id}/artifacts` for previewable outputs.
- Subscribe to `GET /case-runs/{run_id}/stream` or the existing case stream for
  live updates.
- Render board/checklist views from `GET /cases/{case_id}/board`, which is
  derived from cases step-run state and does not depend on Hermes Kanban.
- Render case variables from `GET /cases/{case_id}/board.variables`; do not
  label the whole list as constants. The API classifies variables as
  `dispatcher_input`, `produced_output`, `pending_output`, or
  `deprecated_or_unreferenced` so the UI can distinguish root inputs, filled
  derived values, expected-but-pending outputs, and stale contract slots.

Events and logs carry IDs, hashes, lengths, summaries, and artifact references.
They must not store raw prompts, raw model responses, auth payloads, API keys, or
secret environment values.

## Native Execution

The initial native runner performs:

- Step 1: materialize review assets and commit root review outputs.
- Step 2: call `stt-http` directly and commit transcript outputs.
- Step 3: perform deterministic component-name baseline resolution.
- Steps 4-7: run the current structured analysis baseline behind the native
  runner boundary.
- Step 8: update review status through the gateway using the repository-owned Review Case Automaton contract in `docs/operations/review-case-automaton.md`. Ready packets become public `processed` with automaton status `succeeded`; degraded/non-ready packets become terminal public `failed` with automaton status `failed`. Step 8 does not retry, rerun, or enter a fix loop.
- Step 9: write a daily-note compatibility artifact.

Frank startup scans open or in-progress cases with
`runtime.mode = native_case_pipeline` and schedules incomplete native pipelines
again. Step and run idempotency keys prevent duplicate run/step records during
retry.
