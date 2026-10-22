# Combined component health

Hub's operator health command now invokes Frank's public `python -m frank_executor health --config` command when an explicit reviewed invocation is supplied. The `hub.process-composition.v1` object stays unchanged. Config paths, Python selection and artifact pins are operator CLI arguments, not browser-supplied endpoints or fields in a business record.

Without that invocation, Frank remains `live: null`, `ready: false`, with `executor_health_command_required`. Runtime and service probes still run. With the invocation, Hub checks the Python digest and both wheel digests before launch. The isolated child checks installed versions, wheel file bytes and package import origins against its own prefix before importing Frank. It receives no inherited credentials, `PYTHONPATH` or working-directory source override.

The Frank config must be an absolute regular file owned by the invoking user with no group/other access, under a similarly private direct parent directory. The config and public artifact paths are used only in the local invocation. Responses never include those paths, config contents, integration identifiers, raw subprocess errors, proofs or tokens. The pinned Python may be a normal virtual-environment symlink; operator custody of its target and directory ancestors remains a deployment qualification responsibility.

```sh
python -m services.process_composition health \
  --config config/process-execution.components.json \
  --frank-config "$PRIVATE_FRANK_CONFIG" \
  --frank-python "$REVIEWED_INSTALLED_PYTHON" \
  --frank-python-sha256 "$REVIEWED_PYTHON_SHA256" \
  --frank-wheel "$REVIEWED_FRANK_WHEEL" \
  --frank-wheel-sha256 "$REVIEWED_FRANK_WHEEL_SHA256" \
  --runtime-wheel "$REVIEWED_RUNTIME_WHEEL" \
  --runtime-wheel-sha256 "$REVIEWED_RUNTIME_WHEEL_SHA256" \
  --health-timeout 5
```

Every invocation argument is required together. Each probe has a positive timeout of at most ten seconds. Subprocess stdout and stderr are capped while reading at 16 KiB each; timeout or excessive output terminates the owned process group. Nonzero exit, unexpected stderr, malformed/duplicate/oversized JSON, unsupported health shape, artifact mismatch and unavailable private config all return fixed owner/recovery diagnostics. The subprocess always selects the public health action; there is no arbitrary shell, command or endpoint override.

Runtime and service health bodies are reduced to known storage/authority/verification statuses; arbitrary check details are discarded. A readiness label without supported storage and authority checks fails closed. Liveness failure also prevents readiness. Diagnostics distinguish component storage, native authority, endpoint, adapter, public contract and invocation failures and name the owner responsible for recovery.

Frank's shipped CLI checks adapter lifecycle and configuration but does not contact Runtime. Hub reports that value separately as `cli_ready` and requires Runtime readiness before reporting effective Frank readiness. `native_authority_qualified` remains false. Production composition rejects local-dev readiness even if a test Frank config says ready. Successful effect-disabled probes therefore describe local liveness and compatibility, not live native authority, model/STT/provider quality, confirmed external effects or rollout approval.

The child forwards no ambient secret environment. A configured real adapter that requires credentials consequently remains unavailable unless its owner supplies a separately qualified supported health path. The tests use empty effect-disabled registries and disposable loopback responses, with no provider connection or business-store mutation. Health does not issue native evidence, install trust, create a journal or dispatch work.

Targeted reproduction uses the reviewed installed Python and immutable wheels:

```sh
HUB_HEALTH_INSTALLED_PYTHON="$REVIEWED_INSTALLED_PYTHON" \
HUB_HEALTH_FRANK_WHEEL="$REVIEWED_FRANK_WHEEL" \
HUB_HEALTH_RUNTIME_WHEEL="$REVIEWED_RUNTIME_WHEEL" \
PYTHONPATH="$HUB_CHECKOUT" \
"$REVIEWED_INSTALLED_PYTHON" -m pytest -c "$EXTERNAL_PYTEST_CONFIG" \
  "$HUB_CHECKOUT/tests/test_process_executor_health.py" \
  "$HUB_CHECKOUT/tests/test_process_composition.py"
```

Hub code comes from its checkout for this local implementation review. Frank and Runtime code come from their retained installed wheels; the health child uses isolated imports and verifies their installed bytes. This evidence does not qualify a deployed Hub artifact or any live provider/native identity.

## Artifact-qualified test mode

The actual installed-CLI tests require `HUB_HEALTH_INSTALLED_PYTHON`, `HUB_HEALTH_FRANK_WHEEL`, and `HUB_HEALTH_RUNTIME_WHEEL`. Set `HUB_HEALTH_REQUIRE_ARTIFACTS=1` for qualification; missing artifacts then fail the test run. Ordinary baseline CI without this separately supplied tuple reports explicit skips for those tests. This keeps baseline CI usable without pretending that an unavailable tuple was qualified.
