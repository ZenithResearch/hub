# Hub Documentation Map

This directory contains current architecture/operations docs and older planning notes. Treat this file as the connection layer between root orientation, service READMEs, and runbooks.

## Start by intent

| If you need to... | Open |
|---|---|
| Understand the repository shape | `../README.md` |
| Find a service owner, port, or protocol | `../services/README.md` |
| Understand Gateway HTTP routes and review auth | `gateway-http.md` and `../services/gateway_http/README.md` |
| Understand Frank native review execution | `frank-native-case-pipeline.md` and `../services/frank/README.md` |
| Understand case dispatch / Cases observability | `case-dispatch-review.md`, `frank-native-case-pipeline.md`, `../services/cases/README.md` |
| Run or roll production | `operations/production-rollout.md`, `operations/operator-updates.md`, `operations/production-source-ledger.md`, `../infra/aws_baseline_80/DEPLOYMENT.md` |
| Understand local runtime-state privacy | `local-runtime-state.md` |
| Work on ElevenLabs/local Whisper STT | `ops/elevenlabs-stt-rollout.md`, `../services/stt_http/README.md`, `../services/frank/README.md` |
| Read historical implementation plans | `plans/` |

## Current docs

- `gateway-http.md` — Gateway route map, review auth/session flow, middleware, and source layout.
- `frank-native-case-pipeline.md` — Frank native case-pipeline runtime and Cases observability contract.
- `frank-sophia-runtime-transition-layers.md` — Frank/Sophia boundary notes.
- `case-dispatch-review.md` — case dispatch review notes.
- `local-runtime-state.md` — tracked vs ignored runtime-state boundary.
- `hermes-forward-strategy-report-2026-04-29.md` — strategic/background report.

## Operations docs

- `operations/production-rollout.md` — standard clean-source image build and Terraform rollout sequence.
- `operations/matrix-static-landing-rollout.md` — digest-pinned, Synapse-only landing-page rollout and live smoke procedure.
- `operations/operator-updates.md` — operator-owned production-state doctrine and update planner boundaries.
- `operations/production-source-ledger.md` — non-secret source/image/live-state ledger for main-head deployability.
- `operations/review-case-automaton.md` — review status automaton used by Frank/Gateway status writeback.
- `ops/elevenlabs-stt-rollout.md` — STT provider rollout/runbook.

## Historical plans

`plans/` contains dated planning notes. Use them for rationale and archaeology, not as authoritative current procedure unless a current README or operations doc links to them.

## Documentation rules

- Root README stays broad and path-oriented.
- Service READMEs own service-specific ports, env vars, dependencies, and tests.
- Operations docs own deploy/runbook procedures.
- Historical plans should not be linked as current truth unless they have been revalidated.
- Never commit raw tokens, tfvars values, bearer strings, real access codes, DB passwords, or local runtime artifacts.
