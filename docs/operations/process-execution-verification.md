# Isolated process execution verification and rollback drill

Run this from the Hub checkout with an isolated environment containing the reviewed component wheels. Installed mode verifies every wheel member against installed bytes, all packaged Python module origins, dependency content and the optional native verification helper before starting providers. Source mode remains available for implementation tests.

```sh
/path/to/isolated-env/bin/python -I scripts/prepare_process_artifact_tuple.py \
  --runtime-wheel /path/to/reviewed/process_runtime-0.1.0-py3-none-any.whl \
  --service-wheel /path/to/reviewed/service_management-0.1.0-py3-none-any.whl \
  --frank-wheel /path/to/reviewed/frank_executor-0.1.0-py3-none-any.whl \
  --native-helper /path/to/pinned/verification-only-helper \
  --output /private/tmp/reviewed-artifact-tuple.json
/path/to/isolated-env/bin/python -I scripts/verify_process_execution.py \
  --artifact-manifest /private/tmp/reviewed-artifact-tuple.json \
  --config config/process-execution.components.json \
  --output /private/tmp/process-execution-verification.json
/path/to/isolated-env/bin/python -I scripts/challenge_process_artifact_tuple.py \
  --artifact-manifest /private/tmp/reviewed-artifact-tuple.json \
  --config config/process-execution.components.json \
  --output /private/tmp/process-artifact-challenges.json
```

The manifest freezes an already installed tuple; preparation performs no dependency installation and grants no authority. Installed mode refuses all component source-path overrides. Its file inventory is a content lock for this isolated installation, not a platform wheelhouse, Linux image or native issuing-chain qualification. Retain the exact input wheels/helper and manifest as evidence. Native helper pins refer to a verification-only artifact and do not establish qualification.

For source verification:

```sh
/path/to/process-runtime/.venv/bin/python scripts/verify_process_execution.py \
  --runtime-repo /path/to/process-runtime \
  --service-repo /path/to/service-management \
  --frank-repo /path/to/frank \
  --config config/process-execution.components.json \
  --output /private/tmp/process-execution-verification.json
```

This is local implementation verification. It requires no VM, installed native security migration, real credentials, Hermes instance, Matrix room, STT service or operator vault. All state resides in a fresh private temporary directory, and only loopback HTTP is used. Owned provider subprocesses are stopped and their temporary stores are removed when the drill ends. The selected output file contains the retained public evidence report.

The report records the three independently configured package versions and contract hashes, plus Hub's package version, composition source hash and configuration hash. It retains a file-by-file SHA-256 manifest of the component Python sources, public schemas and package metadata, and fails if those files change during the drill. These are source/check-out verification pins; they do not claim signed release or deployment qualification. Hub's selected live endpoints and secret references are never used by the drill. Providers bind newly allocated loopback ports, use separate component stores and explicitly identify authority as `local_dev_untrusted`.

## Checks

1. Publish a typed runtime process through the public Runtime SDK. Start Frank with an adapter implementing the ordinary `frank.integration.v1` protocol. The adapter only returns deterministic values; it performs no network or business action.
2. Claim work, restart the runtime process and Frank journal, recover the same current claim through `attempt.get`, complete it and read output plus receipt through public queries. Restart again and verify that the output and completion event remain available.
3. Let a short lease expire. Confirm a new Attempt and higher fence, and reject the old completion through the public command API.
4. Lose an integration acknowledgement. Restart both sides, recover the current claim, and verify that the retained unknown effect causes no second send.
5. Deliver an acknowledgement and later success, then redeliver the same success. Confirm one completion and no repeated send.
6. Use Service's public HTTP command/query routes for intake, qualification, acceptance, conversion and approved plan selection. Acceptance before plan selection must remain `awaiting_plan`.
7. Lose the runtime acknowledgement after the Service outbox handoff committed. Restart Service and replay the byte-identical retained command through the public Runtime SDK. Confirm one run, then execute it with the same Frank protocol and read the derived projection while the canonical Case stays planned.
8. Publish an explicitly selected second process version, approve a CasePlanRevision, carry forward one selected committed output after receipt validation, and create a distinct replacement run. Preserve the old pin, output and two-entry revision history.
9. Start separate production-default Runtime and Service instances and prove that untrusted input is denied. Prove Frank refuses unavailable production authority/result verification.
10. Apply a copied test composition's `legacy_read_only` mode through the runtime-owned admission operator with reviewed composition and policy digests. The actual receiver rejects new run/handoff creation while allowing exact committed retries and existing work. Preserve that policy across restart, complete in-flight execution and keep a new Service handoff pending with retryable `not_ready`. Reopen admission with CAS and resume that exact handoff once. Retained outputs are identical and no extra integration send occurs. Unsupported/ambiguous legacy inventory stays ineligible.

The drill also publishes a two-root/join process, races six Frank claimants for one root, executes both roots concurrently, and checks that the join receives both committed outputs with a matching receipt. The reported check groups combine these checks into ten results. Service bootstrapping constructs its own public `Service`, HTTP ingress, SDK adapter, outbox worker and projection. Consumers never read a foreign component's database. The runtime is launched and restarted through its CLI. Fault injection wraps public transport calls only: no rows, receipts, locks or credentials are edited to manufacture a result.

The rollback exercise validates the actual receiver-owned admission consumer and preserved runtime evidence. Its shared lock spans the admitted transaction commit; the exclusive operator update waits before closing future admission. It changes only private test configuration and performs no live cutover. The proof applies to this local process/locking namespace; container and production storage/locking behavior remain qualification gates. Existing active/unknown execution remains runtime-owned and requires its ordinary lease/effect reconciliation. Restoring or deleting an old database is never part of rollback.

## Test entry point

```sh
PROCESS_RUNTIME_REPO=/path/to/process-runtime \
SERVICE_MANAGEMENT_REPO=/path/to/service-management \
FRANK_REPO=/path/to/frank \
/path/to/process-runtime/.venv/bin/python -m unittest discover \
  -s tests -p test_process_execution_integration.py -v
```

Set `PROCESS_ARTIFACT_MANIFEST=/absolute/path/to/manifest.json` instead of the three repository variables to run the installed mode. Without either a manifest or all three explicit repository paths, the integration test reports a skip. The command-line harness requires exactly one supported input mode and exits nonzero on failure. It writes `production_ready: false` even when every local check passes.

## Production no-go conditions

The report retains these limits rather than treating fixture results as production readiness:

- Installed generic native Wallet/secS issuing and receiver-stack qualification remain incomplete; no installed native binary is replaced by this drill.
- The dedicated Frank native Wallet identity and file custody have not been provisioned.
- Production command credentials and terminal integration-result verification remain default-deny until qualified adapters and receiver policy are configured.
- Real Hermes/Matrix, review/STT, status and vault operations are outside this drill and have not been exercised here.
- Source and isolated tests do not authorize activation, migration, live grant changes or a production cutover.

The runtime's native schema-4 synthetic receiver tests, process-resource/shadow goldens, Service boundary tests and Frank adapter tests remain separate retained evidence. They should be run alongside this drill when their corresponding source changes; a green cross-component report does not replace those narrower checks.

The separate [contained legacy comparison](legacy-process-shadow.md) executes the real legacy runner against fixture transports, captures its output bytes, and replays an explicit typed migration through the new runtime. That report preserves the classified field/artifact/failure differences and does not claim independent production handler equivalence.

## Compatibility CI

The existing Hub CI job invokes `scripts/check_process_contracts.py` through `scripts/ci_check.sh`. It validates all three pinned public schemas and rejects a changed contract before component startup. `tests/test_process_compatibility.py` submits an incompatible command schema through that same CI shell entrypoint and verifies a failure. `PROCESS_COMPOSITION_CONFIG` selects an explicit reviewed test configuration; normal CI uses the committed configuration. The artifact challenge CLI also rejects wheel/version/dependency/native-helper drift, SDK source overlays and explicit checkout overrides, restoring the isolated dependency byte mutation after the challenge.

These local CI-entrypoint results do not claim a GitHub-hosted run, independent repository publication, immutable Linux images or a supported tuple wider than the retained three-wheel row. The three greenfield repositories have no configured remote; publishing destinations and remote CI receipts remain open.
