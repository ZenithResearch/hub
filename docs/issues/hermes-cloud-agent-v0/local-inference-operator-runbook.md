# Hermes cloud-agent local-inference operations and rollback

This runbook governs the Issue 97 non-production same-node inference proof. It is an operator procedure for the exact Terraform-owned node and the pinned Hermes profile; it is not an alternate deployment mechanism.

## Scope and non-claims

This procedure can verify the installed lock, desired `READY.json`, supervised loopback listener, startup routing validation, restart continuity, no-swap posture, bounded memory observations, and the fixed startup tool-call probe. It does not prove production readiness, production-safe egress, Matrix E2EE behavior, a successful failed-upgrade rollback when no rollback generation is declared, or consequential machine-call authority.

No raw prompts, responses, tokens, environment dumps, or journal output may enter the evidence record. Record only `PASS`, `FAIL`, or `BLOCKED`, UTC timestamps, public source revisions, approved SHA-256 values, booleans, bounded counters/durations, and the hosted CI URL. Matrix is conversational ingress only; secS remains the future machine-authority boundary.

## Preconditions

1. Use the approved AWS account, region, AMI, `m7i.2xlarge` node, KMS key, EBS volume, and private-S3 object versions from the reviewed Terraform plan.
2. Reach the node only through the fixed Systems Manager administration path. Do not enable SSH or generic Hermes HTTP ingress.
3. Confirm the deployed repository head contains C4.4 commit `ae110cabe8859e782851070d2e16a32b6043eb79` or a reviewed descendant.
4. Keep credentials out of command arguments, shell history, copied output, and evidence. Do not run `systemctl show Environment`, `env`, `set`, or unbounded `journalctl` capture.
5. Create any operator scratch directory with mode `0700`, retain only bounded redacted results, and delete scratch response bodies on success or failure.

## Start from the exact desired generation

The preparation unit validates exact S3 versions, sizes, digests, archive members, installed runtime bytes, model bytes, and the declared desired generation before writing `READY.json`.

```bash
sudo systemctl stop hermes-cloud-agent.service
sudo systemctl stop hermes-inference.service
sudo systemctl restart hermes-inference-prepare.service
sudo systemctl is-active hermes-inference-prepare.service
sudo jq -e --slurpfile lock /etc/hermes-cloud-agent/local-inference.lock.json '
  .active_role == "desired"
  and .runtime.commit == $lock[0].desired.llama_cpp.commit
  and .runtime.archive_sha256 == $lock[0].desired.llama_cpp.archive_sha256
  and .model.model_id == $lock[0].desired.model.model_id
  and .model.sha256 == $lock[0].desired.model.sha256
' /var/lib/hermes/inference/READY.json >/dev/null
sudo systemctl start hermes-inference.service
sudo systemctl is-active hermes-inference.service
```

A failed preparation or inference start is `FAIL`; do not start Hermes, select another model, or configure a remote provider.

## Verify bounded lock, READY, and served identities

The only accepted endpoint is `http://127.0.0.1:8080/v1`, with model alias `qwen3-8b-q4-k-m`.

```bash
sudo sha256sum /etc/hermes-cloud-agent/local-inference.lock.json
sudo jq -er '{active_role,lock_sha256,runtime:{commit,archive_sha256},model:{model_id,sha256}}' \
  /var/lib/hermes/inference/READY.json
curl --noproxy '*' --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:8080/v1/models \
  | jq -e '.data | length == 1 and .[0].id == "qwen3-8b-q4-k-m"' >/dev/null
```

Compare the lock digest with `READY.json.lock_sha256`; compare the runtime commit/archive digest and model ID/digest with the reviewed lock. Record only equality booleans and approved public digests. Do not copy the whole lock, full server response, local paths, or process output into evidence.

## Prove loopback-only listening

```bash
main_pid=$(sudo systemctl show hermes-inference.service -p MainPID --value)
unit_cgroup=$(sudo systemctl show hermes-inference.service -p ControlGroup --value)
test "$main_pid" -gt 1
test -n "$unit_cgroup"
listener=$(sudo ss -H -ltnp 'sport = :8080')
test "$(printf '%s\n' "$listener" | grep -c .)" -eq 1
printf '%s\n' "$listener" | grep -F '127.0.0.1:8080 ' >/dev/null
listener_pid=$(printf '%s\n' "$listener" | sed -nE 's/.*pid=([0-9]+).*/\1/p')
test "$listener_pid" -gt 1
listener_ppid=$(sudo ps -o ppid= -p "$listener_pid" | tr -d '[:space:]')
listener_cgroup=$(sudo sed -n '/^0::/p' "/proc/$listener_pid/cgroup")
test "$listener_ppid" = "$main_pid"
test "$listener_cgroup" = "0::$unit_cgroup"
unset listener listener_pid listener_ppid listener_cgroup main_pid unit_cgroup
```

Acceptance requires exactly one listening socket at numeric `127.0.0.1:8080`, owned by the direct `llama-server` child of the supervisor `MainPID` inside the exact inference unit cgroup. The command emits no process listing into evidence; retain only the comparison booleans. Wildcard, IPv6 wildcard, non-loopback, additional-port, wrong-parent, wrong-cgroup, or absent-listener evidence is `FAIL`. Do not widen the security group, systemd address policy, or bind address to recover service.

## Validate exclusive Hermes routing before Matrix credentials

Do not invoke `hermes-validate-local-routing` manually as root; that would not reproduce the service identity or ordering. Restart the gateway through systemd so its runner executes `/usr/local/libexec/hermes-validate-local-routing` as `hermes` with the service-only `hermes-inference` supplementary group before calling the Matrix secret reader.

```bash
sudo systemctl restart hermes-cloud-agent.service
sudo systemctl is-active hermes-cloud-agent.service
sudo systemctl show hermes-cloud-agent.service \
  -p User -p Group -p SupplementaryGroups
```

Acceptance requires `User=hermes`, primary `Group=hermes`, supplemental `hermes-inference`, and an active service. A routing mismatch, credential file, provider environment override, proxy variable, nonlocal URL, alternate model, fallback, `/model`/`/moa` override, unreadable attestation, or Matrix-secret read before validation is `FAIL`.

## Restart-continuity proof

Capture only bounded pre/post identity fields and checksums in the mode-`0700` scratch directory.

```bash
sudo sha256sum /var/lib/hermes/inference/READY.json
sudo systemctl restart hermes-inference.service
sudo systemctl is-active hermes-inference.service
sudo sha256sum /var/lib/hermes/inference/READY.json
sudo systemctl restart hermes-cloud-agent.service
sudo systemctl is-active hermes-cloud-agent.service
```

The pre/post desired `READY.json` identity must agree, the inference listener must return under the configured startup deadline, and the gateway must become active only after local routing validation. Changed bytes, rollback activation, remote routing, or an unexplained identity change is `FAIL`.

## G4.1 no-swap fit and Hermes-compatible tool-call proof

Run this section only on the declared `m7i.2xlarge`. The inference supervisor does not report ready until its bounded OpenAI-compatible request receives the expected `health_probe` tool-call shape from the exact local model. Restarting the service therefore executes a fresh fixed tool-call probe without publishing the prompt or response.

```bash
imds_token=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
  --request PUT \
  --header 'X-aws-ec2-metadata-token-ttl-seconds: 60' \
  http://169.254.169.254/latest/api/token)
instance_type=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
  --header "X-aws-ec2-metadata-token: $imds_token" \
  http://169.254.169.254/latest/meta-data/instance-type)
instance_id=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
  --header "X-aws-ec2-metadata-token: $imds_token" \
  http://169.254.169.254/latest/meta-data/instance-id)
bound_instance_id=$(sudo jq -er '.instance_id' /var/lib/hermes/.active-instance)
test "$instance_type" = 'm7i.2xlarge'
test "$instance_id" = "$bound_instance_id"
unset imds_token instance_type instance_id bound_instance_id
sudo systemctl reset-failed hermes-inference.service
sudo systemctl restart hermes-inference.service
sudo systemctl is-active hermes-inference.service
swapon --show --noheadings
sudo systemctl show hermes-inference.service \
  -p MemoryCurrent -p MemoryPeak -p MemoryMax -p TasksCurrent -p TasksMax
```

Before entering the node, the operator must also confirm that the SSM target instance ID equals the reviewed `hermes_cloud_agent_instance_id` Terraform output. The on-node IMDSv2 check then binds the measured type to the same instance recorded in `.active-instance`; publish only the two equality booleans, never the raw instance ID or metadata token.

Acceptance requires an empty `swapon --show` result, successful semantic readiness/tool shape, `MemoryPeak` below `MemoryMax`, tasks below `TasksMax`, and completion within the reviewed readiness deadline. Record only bounded numeric values and booleans. If the target/type binding fails, swap is present, readiness exceeds the bound, the probe fails, or resource use reaches the limit, mark G4.1 `FAIL` and stop for a reviewed model/runtime/resource decision; remote fallback is forbidden.

## Wrong-byte and wrong-config rejection

Do not corrupt live model, runtime, lock, profile, config, or `READY.json` bytes to manufacture negative evidence. Reproduce the fail-closed cases in a clean checkout with the repository-owned fixtures:

```bash
uv run --frozen --offline --extra dev -- pytest -q \
  tests/test_hermes_cloud_agent_inference_prepare.py \
  tests/test_hermes_cloud_agent_inference_service.py \
  tests/test_hermes_cloud_agent_local_routing.py \
  tests/test_hermes_cloud_agent_contract.py \
  tests/test_hermes_cloud_agent_iac.py
python3 scripts/verify_hermes_cloud_agent_pinned_routing.py --hermes-source /path/to/clean/pinned/hermes
```

The pinned-source path must be a clean checkout at `3ef6bbd201263d354fd83ec55b3c306ded2eb72a`. Pytest is part of the reviewed project `dev` extra and `uv.lock`; do not add an ad hoc `--with` overlay. Provision the exact lock-backed package cache before disconnecting network access; `--frozen --offline` must return `BLOCKED` rather than resolve or download mutable dependencies. Test or verifier failure is `FAIL`, never evidence to bypass validation. Exact-head hosted CI remains the authoritative networked repository gate.

## Failed desired upgrade and declared rollback

The current artifact lock has no declared rollback generation. It also has no safe in-place lock rollout mechanism: the lock is embedded in EC2 user data, `user_data_replace_on_change = true` replaces the instance, and the persistent state binding correctly rejects that replacement identity. Therefore live failed-upgrade rollback evidence is `BLOCKED`, and C4.5 remains incomplete until separately reviewed work provides both a previously accepted complete rollback generation and a bounded deployment/recovery transition.

The required lock rollout mechanism must stop the gateway and inference units, schema-validate and atomically install one reviewed root-owned lock, bind the lock to an approved repository revision and digest, reject caller-selected commands/paths/URLs or partial generations, preserve the existing instance/volume/device binding, and record only bounded revision/digest/status evidence. It must also emit a closed, non-sensitive pre-secret failure reason such as `routing_ready_generation_mismatch` from the same gateway startup path. If replacement is intended instead, it requires the separately reviewed state-binding recovery transition; clearing or rewriting `.active-instance` is forbidden. Neither mechanism nor the causal failure attestation exists in the current candidate, so the exercise below is not executable yet.

Do not edit the installed lock, `READY.json`, digest-addressed generation directories, symlinks, or systemd units on the node. Do not treat an undeclared previous file as rollback authority.

A future approved rollback exercise, after that mechanism lands and is independently verified, must:

1. install through the bounded lock rollout mechanism a reviewed lock containing the complete desired generation and one complete declared rollback generation;
2. preserve exact immutable S3 version IDs, sizes, digests, source revisions, model ID, context, template, and license for both generations;
3. keep Terraform/repository state authoritative and deploy through the reviewed bounded lock rollout mechanism rather than manual node mutation or user-data replacement;
4. induce the failed desired upgrade only with an operator-approved non-production exact-version fixture;
5. stop the gateway and inference units, run `systemctl restart hermes-inference-prepare.service`, and require the preparer to validate the already installed declared rollback before publishing `active_role: rollback`;
6. run the validator as the actual unprivileged service identity with only the service supplementary group and require the exact bounded failure before any Matrix secret read:

   ```bash
   profile_home=$(sudo jq -er '.profile.home' /etc/hermes-cloud-agent/profile.json)
   pinned_model=$(sudo jq -er '.inference.model_id' /etc/hermes-cloud-agent/profile.json)
   pinned_url=$(sudo jq -er '.inference.base_url' /etc/hermes-cloud-agent/profile.json)
   set +e
   validation_output=$(sudo runuser -u hermes -g hermes -G hermes-inference -- env -i \
     HOME="$profile_home" HERMES_HOME="$profile_home" \
     HERMES_STRICT_LOCAL_MODEL_ROUTING=1 \
     HERMES_PINNED_MODEL="$pinned_model" HERMES_PINNED_BASE_URL="$pinned_url" \
     /opt/hermes/venv/bin/python /usr/local/libexec/hermes-validate-local-routing 2>&1)
   validation_status=$?
   set -e
   test "$validation_status" -eq 1
   test "$validation_output" = \
     'local routing validation failed: local routing READY generation mismatch'
   unset validation_output validation_status profile_home pinned_model pinned_url
   ```

7. do not attempt the real gateway start as accepted rollback evidence until the startup path emits and the evidence probe validates the closed `routing_ready_generation_mismatch` reason before Matrix secret retrieval. A generic failed unit is not causal evidence: `Result` and `ExecMainStatus` alone cannot distinguish routing rejection from an unrelated startup failure. This missing bounded attestation keeps the exercise `BLOCKED`.
8. after that attestation lands, require the real systemd start to fail with the exact bounded reason, prove no Matrix secret retrieval or remote-provider switch occurred, then restore a valid desired generation through another reviewed lock rollout and repeat exact identity, restart, no-swap, and tool-shape checks.

Any implicit, last-file-wins, manually edited, digest-mismatched, undeclared, or partially validated rollback is `FAIL`.

## Evidence disposition

Use these terminal states:

- `PASS` — the exact check ran against the declared target and all bounded assertions passed.
- `FAIL` — the check ran and a required assertion failed; stop the dependent transition.
- `BLOCKED` — required approved target, credentials, declared rollback, or operator authorization is absent; do not simulate success.

C4.5 may close only after G4.1 passes on the declared node, a declared rollback exercise is no longer `BLOCKED`, the full local/static gate passes, exact-head reviewers approve, and hosted CI passes. Task 5 live execution remains separately gated.

## Stop and recovery rules

- Stop on identity disagreement, non-loopback listening, provider credential/fallback discovery, swap use, resource-bound failure, unexpected rollback, missing operator authority, or evidence that would contain sensitive material.
- Do not edit root-owned runtime state to recover service.
- Do not weaken E2EE, routing validation, systemd isolation, S3 version pinning, digest checks, or no-fallback behavior.
- Use [`state-recovery-runbook.md`](state-recovery-runbook.md) for Matrix state compromise, instance replacement, or teardown.
- Leave the profile disabled or failed closed until the responsible operator resolves the blocker through a reviewed change.
