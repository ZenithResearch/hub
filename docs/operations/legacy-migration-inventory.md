# Read-only legacy migration inventory

`services.process_composition.migration` assesses a deliberately selected frozen copy of the legacy Cases SQLite database. It creates no ServiceRequest, Case, ProcessRun, output, effect or migration record. Every report has `production_ready: false` and `migration_authorized: false`; an eligible entry is only a candidate for a future newly pinned run after operator review and fresh admission checks.

The collector does not call legacy HTTP inspection routes: `GET /cases/{id}` can recompute stored state. It opens the selected absolute, nonsymlink, journal-free database with SQLite `mode=ro&immutable=1`, checks the complete known table/column inventory and foreign keys, bounds database/case/record/JSON sizes, and compares database SHA-256 before and after collection. A nonempty WAL or rollback journal, unknown legacy schema, incomplete tables, inconsistent references or a changing source fails closed. Obtain and review a consistent frozen backup through existing operational procedures first; this module does not implement a backup or migration write protocol. The collector must never be pointed at a live database that can change underneath an immutable reader.

## Source evidence and compatibility

Collected dictionaries carry an immutable original full-content digest outside their JSON fields. Assessment detects any subsequent edit. Serialized JSON loses this in-process pin and is denied unless trusted review code supplies the independently retained full digest through `expected_evidence_sha256`. Select that digest from the reviewed source collection manifest; never compute it from an incoming caller payload or accept a payload field as the expected pin. The per-Case map used by `assess_inventory` is the same trusted port. Hashes prove consistency with selected bytes, not legacy authentication, a cryptographic receipt, signer identity or migration approval. Trusted backup selection remains an operational responsibility.

```python
from services.process_composition.migration import collect_legacy_evidence, assess_inventory

snapshots = collect_legacy_evidence("/absolute/operator-selected/frozen-cases.sqlite", max_cases=1000)
report = assess_inventory(snapshots)
```

The existing `legacy_eligibility(snapshot)` summary keeps its `{eligible, reason, mode}` result shape. A collector-produced valid root-only snapshot can return `create_new_run`; historical label/hash-only dictionaries now fail closed. For archived evidence the wrapper accepts the additive keyword `expected_evidence_sha256`. Existing composition configuration and component wire contracts are unchanged. Old stored contracts and historical records remain readable by the legacy owner; unsupported inventory is never repaired, rewritten or marked complete by this module.

## Evidence checked before disposition

The assessor hashes actual retained process source and the full frozen contract, compares the stored contract with the actual compiler result, and uses the legacy execution validator for the typed DAG, variable producers/consumers, step identities and supported embedded handler profile. Explicit root intent must bind the original `case.dispatch_packet_json.initial_context`, queue message, Case identity, complete declared root set and typed values. Populated root slots must agree with that source-owned intent and cannot claim an output attempt.

The closed inventory includes Case/step/slot rows, CaseRuns and StepRuns, current claim, claim request records, external effects, artifact metadata, events, spans, logs and model audit references. Attempts must correlate with actual run identity, supported protocol, complete contiguous fence history and exact prior-attempt chain. Claims bind the latest fence, runner, request key, finite lease and active/released state. Active or expired claims and unfinished attempts require reconciliation. Missing attempts/claim records, unknown states, booleans used as integers, future timestamps, malformed JSON and foreign or cross-run references cannot establish eligibility.

Ordinal step completion or skip cannot create successful outputs. Retained outputs require typed values matching the actual committed result, runtime state, all declared output slots and a matching terminal StepRun/run/fence and exact commit timestamps. Even those correlated historical rows have `native_receipt_verified: false` and `carry_forward_allowed: false`. Effect identities must match the legacy deterministic run/step/operation binding and actual handler; unknown or observed effects require reconciliation, and a terminal external-effect step without committed effect inventory cannot be eligible. Artifact URIs are never fetched; reported hashes and sizes remain declarations with `bytes_verified: false`.

| Disposition | Meaning |
| --- | --- |
| `new_root_run_candidate` | Actual supported frozen contract, explicitly bound root intent, root-only state and no execution/output/effect history. Review may propose a new run; this report grants no execution authority. |
| `reconciliation_required` | Active/expired lease, unfinished or prior execution, unknown/observed external effect, incomplete committed effect inventory or other ambiguous progress. Keep owner reconciliation with the legacy execution record; never resend an effect from inventory. |
| `read_only_unsupported` | Missing/unpinned provenance, unsupported contract/schema, ordinal labels, unvalidated outputs or inconsistent evidence. Preserve original records for inspection. |
| `read_only_terminal` | Terminal history with no unresolved execution/effect condition. Preserve results and historical evidence; do not restart it. |
| `compatibility_revision_required` | Correlated retained output/artifact history needs an explicit reviewed compatibility revision before any future reuse. Inventory never carries it forward. |

## Local validation and remaining gates

The local fixtures use the real legacy compiler, API claim/output/effect/finish transitions and SQLite schema in disposable directories. Tests prove a real root-only candidate, reject recomputed caller hashes and erased attempt/effect history, distinguish active/expired leases and retained/terminal states, and compare database file bytes, schema, log rows and directory contents before/after read-only collection. The retained six-Case fixture inventory records one root candidate, two reconciliation cases, one unsupported ordinal case, one terminal case and one output compatibility case. No live database or provider was accessed.

Evidence and exact source/command hashes are retained in `/private/tmp/process-completion-execution-20261013/migration-inventory.json` and its referenced fixture archive. These are local implementation/review evidence. Live inventory reconciliation, source backup provenance, target process/version/input pins, explicit output compatibility, qualified native identities/issuance/receiver/result verification, real adapter/effect qualification and an operator-approved go/no-go remain separate gates. Native production and external-effect gates stay closed. The inactive Devgraph grant was not renewed; no canonical Task or Issue completion is claimed by this module.
