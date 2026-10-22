# Legacy review containment

This is a safety boundary around the retired Hub-native personal review runner. It is not the greenfield process-runtime or service-management implementation. Hub's existing Cases rows remain legacy migration data; no new canonical ProcessRun or Case-binding aggregate is introduced.

## Frozen execution eligibility

New Markdown contracts must have unique contiguous positive legacy step identities, supported variable types, consistent typed IO, and a complete DAG without missing, self, backward, cyclic or duplicate edges. Each edge must bind exactly the variables produced and consumed by its endpoints; missing and duplicate bindings fail closed. Legacy positional edges retain their existing JSON shape for readers. The generic greenfield runtime is not constrained by this old positional schema.

`base/ops/processes/resources/personal-review-v1.json` contains the versioned handler mappings and handler prerequisites. Publishing the bundled Markdown embeds the resource in its contract snapshot. A retained pre-containment source hash selects the explicit compatibility resource without rewriting the stored snapshot. Unknown handler resources, altered resource versions and malformed contracts cannot execute. Eligibility is checked for every managed mutation, including work with an already active claim. Historical contracts, slots and artifacts remain readable; invalid snapshots are read-only and GET cannot advance their state. Operators can inspect `execution_eligibility` and `GET /cases/{id}/execution`.

The runner derives candidates from compiled dependencies, explicit resource prerequisites, and populated root inputs. Ordinal step names never select runnable work. There is no fallback that invents null outputs for unknown steps. Stored step identities must exactly cover the frozen contract. Completion requires every frozen step to have committed successfully.

## Claim protocol v1

The legacy protocol serializes one runner per Case using SQLite `BEGIN IMMEDIATE`, including the validation and resulting write. Lease/fence validation happens after acquiring the writer lock and again before transaction commit. An output, heartbeat, effect or finish that outlives its admitted lease rolls back entirely. It is intentionally a containment mechanism rather than an independent deployed service.

- `POST /cases/{id}/execution/claim`: `request_key`, `owner`, `lease_seconds` (10–600; default 120), `initial_context` containing typed root inputs only. Returns a new `run_id`, monotonically increasing `fence`, and expiry. Root input binding and the new attempt commit together.
- `POST /cases/{id}/execution/heartbeat`: renew the current lease before expiry.
- `POST /cases/{id}/execution/finish`: a completed, failed, blocked or cancelled attempt result; completion is rejected while steps or effects remain unresolved.
- `POST /cases/{id}/execution/effects`: record `unknown` immediately before sending an external operation, then `observed` with response evidence. Output completion changes observed evidence to `committed` in the same transaction as output and step-attempt completion.
- `GET /cases/{id}/execution`: eligibility, current lease, effective lease activity and retained effect evidence.

Mutation requests carry `X-Legacy-Run-Id` and `X-Legacy-Fence`. These fields are concurrency evidence, **not authentication or authorization**. They do not confer Castalia, Wallet, secS, Matrix, Hermes or Dregg authority. Existing private-network exposure requirements still apply. Production authentication for the greenfield executor is a separate component contract.

Claim retries with the same key and identical payload recover the same live attempt. An expired or released request cannot revive a terminated attempt. A new retry request allocates another `case_run`; its step executions allocate new `step_runs`. Previous rows and output provenance are preserved. Old attempts or pre-existing step/output progress created without lease evidence require reconciliation before activation. Terminal review failure or success cannot be reset through this bridge.

The lease guard covers direct step, slot, status, runtime-state, dispatch, run, span, event and artifact mutation routes. Native root writes occur only at claim; outputs commit through the current step attempt. Generic observability routes remain available for non-native consumers, but `POST /cases/{id}/runs` cannot create native execution. Old native writers lacking a live fence intentionally receive 409.

## External outcomes and artifacts

Transcription, review-status writeback and daily-note compatibility output have deterministic attempt-scoped effect identities. A provider response is observation, not completion: it must be committed with typed outputs. A crash between sending, observing and committing leaves durable unresolved evidence. Neither a lease takeover nor a Frank restart repeats such work automatically.

The native transcription call does not silently retry an ambiguous transport error or switch to another provider. An operator must reconcile unresolved effects before a future migration or approved recovery operation. This bridge deliberately does not provide an unauthenticated endpoint that can declare an unknown effect safe.

Artifacts are written below `case/attempts/<case_run_id>/`. The daily-note compatibility artifact is written once per attempt, not appended on replay. Already committed slots are immutable and keep their original provenance across safe retries.

## Review quality

Status writeback reads the final canonical packet beside the committed review document, within the configured execution root. It recomputes quality and validates structure, including unique feedback identities, typed target references and evidence lists. Explicit source-binding feedback identities must match; conflicting blocked or deferred binding evidence cannot be masked by another verified binding. A packet with review identity must match the current review. A missing, unreadable, malformed, transcript-only, needs-human-review, or needs-source-binding packet writes public `failed` and preserves diagnostic packet status. A stored `quality.status` label cannot override degraded evidence. Only `review_packet_ready` writes public `processed`.

Failure evidence and the failed step outcome commit atomically; downstream success logging cannot run afterward. The public Gateway status enum and existing Cases read fields remain unchanged.

## Verification and rollout

Run the focused baseline plus `tests/test_legacy_containment.py`. The latter exercises concurrent competing claims on one SQLite store, stale mutation paths, exact claim replay, expiry and distinct retries, typed readiness, unknown effects across restart, and subprocess death before/after output commit. Its stores and effects are disposable.

Before rollout, inspect existing execution eligibility and effect evidence. Do not delete or recompile historical Cases to force activation. Rollback can keep the new tables and additive fields for inspection, but must keep native execution disabled: reverting to an unfenced writer is not a safe execution rollback. This change does not deploy components or claim canonical Devgraph Task completion.
