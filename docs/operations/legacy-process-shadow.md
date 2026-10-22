# Contained legacy process comparison

Run this from the Hub checkout using its Python environment (FastAPI and httpx are needed). Only an explicit runtime source path is imported.

```sh
.venv/bin/python scripts/verify_legacy_process_shadow.py \
  --runtime-repo /path/to/process-runtime \
  --output /private/tmp/legacy-process-shadow.json

PROCESS_RUNTIME_REPO=/path/to/process-runtime \
.venv/bin/python -m unittest discover -s tests -p test_legacy_process_shadow.py -v
```

The harness executes the actual legacy `CasePipelineRunner` and Cases ASGI API in temporary stores. It supplies synthetic audio/events, a fixed STT response, a temporary source file and an intercepted status writeback. Any other external transport fails. One scenario resolves source evidence and completes; a second lacks source binding and commits a failed status step with downstream work blocked. Neither scenario reads live cases, keys, operator files or provider credentials. It requires no VM.

The harness captures artifact bytes at each legacy output commit. An explicit normalizer maps those retained results into the published typed personal-review resource and executes them through the new runtime's ordinary command/query interface, with the effect-free fixture harness. The report compares ready batches, selected steps, terminal outcomes, normalized typed output hashes and receipt integrity. It retains the complete replay fixture, raw legacy output diagnostics, artifact bytes, runtime report, compilation pin and hashes of the exact source files. Tests replay the retained fixture after the legacy temporary store has been deleted.

This is an **actual contained legacy execution followed by migrated-output replay**. New production handlers are not independently executed here. Successful replay demonstrates scheduling, typed migration and durable output/receipt behavior; it does not establish production semantic equivalence.

## Explicit migration differences

- Legacy serial selection collapses each remaining subset of a ready batch; the new runtime claims the entire ready batch before completing it. Actual selected handler order is also compared.
- Legacy events and rich nested fields become closed typed values. Complex evidence remains canonical JSON inside declared string fields, nullable strings become empty strings, and the original output is retained as a JSON artifact.
- Legacy step 7 overwrites the packet path produced by step 5. Immutable artifacts require a distinct final packet and status output from step 7, which step 8 explicitly consumes. Both initial and final packet bytes remain available. This dependency is part of the retained process compilation pin.
- A legacy failed status step commits diagnostic outputs. The new runtime's failed Attempt has no successful OutputCommit; its failure receipt is compared while the legacy diagnostic remains in the retained fixture. The downstream daily-note step must not run.
- Legacy fenced terminal rows and new immutable receipts have different envelopes. The comparison checks typed output hashes and new receipt integrity rather than claiming byte equality.
- Legacy wall-clock timings and fixed replay timings are not compared.

Every report is `production_ready: false`. Real integration-owner review of the field migration, independent greenfield handler execution, native identity/authority qualification and an approved live cutover remain separate requirements. Unclassified divergence fails the command; classified differences remain visible even when local checks pass.
