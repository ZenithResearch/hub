# Hub implementation status

This ledger separates repository-backed behavior from incomplete or directional
architecture. It was assessed against Hub commit
`be36dacb325f69a5585a1a7771b7d355aa1aa53e` on 2026-08-17. A source-tree status
does not, by itself, prove that the same behavior is deployed or production-ready.

This document is partial progress on [issue #84](https://github.com/ZenithResearch/hub/issues/84).
The README is unchanged by this documentation slice, so the issue's README and
guard-test acceptance criteria remain open.

## Status labels

| Status | Meaning |
|---|---|
| `implemented` | Present in current source and supported by a focused test. Claims must stay within the behavior that the cited test exercises. |
| `partial` | Some source exists, but an important path, dependency, test, or operational proof is incomplete. Describe the missing boundary whenever this label is used. |
| `planned` | An intended integration or accepted direction that is not present in Hub source and tests at the assessed commit. |
| `future` | Directional work without a current implementation commitment. It must not be treated as a dependency that already exists. |
| `out-of-scope` | A boundary Hub does not own. Hub may configure or call the external component without implementing it. |

## Required claim discipline

- Use **implements** or **consumes** only for an `implemented` row whose cited
  source and test demonstrate the specific claim.
- Qualify `partial` behavior with its missing boundary; a source file, interface,
  fixture, or configuration value alone is not an end-to-end integration.
- Describe `planned` and `future` behavior in the future tense. Do not describe it
  as available to callers or operators.
- Reserve **production-secure**, **capability-backed**, and equivalent security or
  deployment claims for code, focused tests, and relevant runtime evidence.
- Update this ledger in the same change that promotes a capability, and replace
  planning references with links to the landed source and tests.

## Current ledger

| Surface | Status | Repository evidence | Current boundary |
|---|---|---|---|
| Queue HTTP enqueue, dequeue, and payload-minimizing peek | `implemented` | [`inbox/http.py`](../inbox/http.py), [`inbox/store.py`](../inbox/store.py), and [`tests/test_queue_http.py`](../tests/test_queue_http.py) | The focused tests cover HTTP enqueue/dequeue field preservation and the default/legacy peek response shapes; this row does not claim every retry or gRPC path. |
| In-process Eventbus publish/subscribe broker | `implemented` | [`services/eventbus/broker.py`](../services/eventbus/broker.py), [`services/eventbus/http.py`](../services/eventbus/http.py), and [`tests/test_eventbus_broker.py`](../tests/test_eventbus_broker.py) | The tests cover topic delivery, payload round-trip, and clean subscriber shutdown. They do not establish a durable or distributed event log. |
| Cases contract state and execution observability records | `implemented` | [`services/cases/main.py`](../services/cases/main.py), [`services/cases/contract.py`](../services/cases/contract.py), [`tests/test_cases_contract.py`](../tests/test_cases_contract.py), and [`tests/test_cases_observability.py`](../tests/test_cases_observability.py) | Focused tests cover contract-derived cases plus run, step, span, event, artifact, and board records in the local service. Deployment availability is a separate operational claim. |
| Frank native review case pipeline | `partial` | [`services/frank/case_pipeline_runner.py`](../services/frank/case_pipeline_runner.py), [`docs/frank-native-case-pipeline.md`](frank-native-case-pipeline.md), and [`tests/test_frank_case_pipeline_runner.py`](../tests/test_frank_case_pipeline_runner.py) | The service pipeline and focused tests exist, but many tests use controlled client doubles; this is not proof of every external-provider or deployed end-to-end path. |
| Matrix/Synapse-backed Hub messaging | `partial` | [`services/ingest/`](../services/ingest/), [`services/matrix_bridge/`](../services/matrix_bridge/), and the [README's routing caveat](../README.md#matrix-backed-local-community) | Local ingest, bridge, and Synapse surfaces exist. Default routing centers on the `feedback` room, and additional rooms are not yet first-class Hub channels. |
| secS-magik RPC integration in Hub server components | `planned` | [Issue #84](https://github.com/ZenithResearch/hub/issues/84) and the explicit non-claim boundary in [`docs/issues/matrix-synapse-v0/iss-p21-004-ordinary-execution-path.md`](issues/matrix-synapse-v0/iss-p21-004-ordinary-execution-path.md) | No secS-magik RPC client/server integration or focused Hub test was found in the assessed Python source. Do not describe Hub as implementing or consuming this RPC layer. |
| Dregg-backed Hub capability/proof/revocation authority | `planned` | [Issue #84](https://github.com/ZenithResearch/hub/issues/84), the Hub non-claim boundary above, and the separate [secS-magik Dregg authority rail](https://github.com/ZenithResearch/secS-magik/issues/73) | Work in secS-magik does not constitute a Hub integration. Hub has no source-and-test-backed Dregg authority adapter at the assessed commit. |
| Castalia Wallet ownership or credential bridge in Hub | `planned` | [Issue #84](https://github.com/ZenithResearch/hub/issues/84) and the wallet non-claim boundary in [`docs/issues/matrix-synapse-v0/iss-p14-002-core-terraform-resource-adoption.md`](issues/matrix-synapse-v0/iss-p14-002-core-terraform-resource-adoption.md) | References to wallet architecture are directional; no Hub wallet API integration and focused test establish ownership presentation. |
| secZ-mediated capability presentation in Hub | `planned` | [Issue #84](https://github.com/ZenithResearch/hub/issues/84) and the same current Matrix/wallet boundary | No secZ API integration or focused Hub test was found in the assessed source. |
| Stable public SDK and API contracts | `future` | [`README.md`](../README.md#status-active-wip) | The repository explicitly expects breaking changes and is not presented as a stable SDK or production platform. |
| Synapse, Qdrant, and clients Postgres implementations | `out-of-scope` | [`services/README.md`](../services/README.md#backing-services-in-compose) | These are third-party backing services. Hub owns its configuration and integration surfaces, not their implementations. |
