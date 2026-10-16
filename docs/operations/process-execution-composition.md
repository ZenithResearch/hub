# Process execution composition and rollback

Hub owns the component manifest, compatible public contract snapshots, secret references, endpoints and admission flags. It owns no new ServiceRequest, Case, ProcessRun or binding record. Component operational files are created in distinct private directories outside Hub.

`config/process-execution.components.json` pins all three package versions and SHA-256 hashes of public contract artifacts. Contract copies are schemas only. `services.process_composition` rejects mismatched hashes, incomplete component sets, embedded credentials, unsafe endpoints and unqualified production admission. After installing the separately built packages into the integration environment, verify:

```sh
python -m services.process_composition check --config config/process-execution.components.json
python -m services.process_composition check-installed --config config/process-execution.components.json
python -m services.process_composition health --config config/process-execution.components.json
```

Liveness, storage readiness and production authority qualification are separate facts. Frank is an independent executor with its own `check-config` and `health` commands; Hub's manifest does not claim that a reachable HTTP port proves executor readiness. See the cross-component verification command and retained results in `process-execution-verification.md`.

The combined health check can invoke Frank's real installed public CLI using explicit private operator configuration and reviewed Python/wheel pins. It returns bounded redacted owner/recovery diagnostics and accounts for Runtime dependency readiness. See [executor health](process-executor-health.md) for invocation, failure semantics and the effect-disabled qualification boundary.

## Local component wiring

Run Python 3.12 and the pinned wheels. The runtime API binds loopback 8092; service management binds loopback 8091. Their CLI configuration selects separate database files, environment and authority adapter. Frank's `frank.config.v1` selects the runtime endpoint, private evidence journal and enabled integration registry. Matrix and Hermes are configured through adapter entries, never scheduler switches. Matrix tokens and Wallet/receiver references remain outside Hub in operator-selected secret providers.

Development/test requires explicit `local_dev_untrusted` on each participating component. Production leaves that mode disabled and denies execution until a qualified Wallet/secS authority implementation and receiver policy are supplied. The user approved a dedicated Frank identity with Wallet-only native file custody and operator-controlled rotation/revocation; this decision has not provisioned a key. No generic signing oracle or raw seed path is supplied to Frank.

## Reversible admission flags

`shadow` is the default: fixture execution has no external-effect capability. `greenfield` selects the runtime for new process work, subject to authority qualification in production. `legacy_read_only` stops new execution admission while preserving inspection. `change_admission` requires the exact reviewed configuration digest, returns a validated replacement and never modifies component stores. Applying the returned configuration is an operator/composition action.

Rollback changes new-work routing. Existing runs, leases, output commits and effect evidence remain with process-runtime, and Frank uses runtime reconciliation for them. Never copy those aggregates into Hub or hand a partially executed run to the unfenced legacy runner. Old legacy work with unsupported contracts, ordinal progress, active leases, unknown effects or unvalidated outputs remains read-only. Only explicit root intent with validated frozen contract and no execution history can start a newly pinned run; it cannot be translated into completed work.

## Current qualification boundary

Isolated compiler, receiver, storage, HTTP, service handoff and Frank tests do not establish production Wallet machine-identity custody or live provider credentials. A disposable macOS qualification environment is relevant to replacement of the live security stack, not to building these components. The current manifest has `authority_qualified: false` and `external_effects: false`. It supports local review and a rollback drill, not an implicit production cutover. Any eventual live cutover needs reconciled existing work and retained qualification evidence for the exact deployed artifacts.

Current exact-artifact local evidence is linked in [process verification](process-execution-verification.md#current-local-installed-evidence) and [component images](process-component-images.md). The accepted Hub tuple and separate full Frank suite leave this development/shadow composition, `authority_qualified: false` and `external_effects: false` unchanged.
