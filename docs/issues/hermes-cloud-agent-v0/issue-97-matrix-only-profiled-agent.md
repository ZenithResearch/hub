# Issue 97: Deploy one Matrix-only profiled Hermes cloud agent and private admin control plane

> Issue = PR boundary. Tasks below = commit boundaries inside that PR.

## PR boundary

- **GitHub issue:** https://github.com/ZenithResearch/hub/issues/97
- **Branch:** `issue/97-matrix-only-hermes-cloud-agent`
- **Suggested PR title:** `Deploy one Matrix-only profiled Hermes cloud agent`
- **Primary repo:** `ZenithResearch/hub`
- **Repo-local spec:** `docs/issues/hermes-cloud-agent-v0/issue-97-matrix-only-profiled-agent.md`
- **Machine-readable contract:** `infra/hermes_cloud_agent/profile.schema.json`

## Objective

Deploy one isolated non-production Hermes profile on one AWS machine and add the private Hub control-plane seam needed to administer it. Matrix is its only conversational ingress, llama.cpp inference runs on the same machine, durable profile and Matrix crypto state survive restart, and no generic Hermes HTTP/API control surface is exposed.

Matrix remains available for human-agent and agent-to-agent conversation. Consequential machine operations require a later secS boundary and cannot be authorized by Matrix identity, room membership, or message text.

The Hub Agent Admin Service is an operator/resource-control surface, not agent ingress. Its internal gRPC contract sits behind the authenticated Gateway admin HTTP edge and exposes bounded profile registration, desired and observed state, status, restart/disable requests, credential-reference lifecycle, and redacted evidence. It does not accept prompts or arbitrary tool calls, and the generic Hermes HTTP/API control surface remains disabled.

## Architecture boundary

```text
ZenithOS operator
  -> authenticated Gateway admin HTTP edge
  -> Agent Admin Service (internal gRPC)
  -> desired/observed profile registry + AWS Systems Manager operations
  -> private profiled cloud node

Humans/agents
  -> Matrix/Synapse E2EE
  -> Hermes Matrix adapter
  -> local llama.cpp inference

Authorized machines (later)
  -> secS verifier/policy
  -> one declared bounded handler
```

Terraform owns the private node, encrypted storage, IAM, networking, and bootstrap resources. The admin service reconciles operational state against that IaC-owned inventory; it does not silently mutate Terraform state or create an alternate provisioning authority.

## Locked threat boundary

### Trusted only for bounded purposes

- AWS IAM instance identity: retrieve explicitly permitted runtime secrets and use Systems Manager.
- Dedicated Hermes profile directory: durable state for one profile only.
- Dedicated Matrix account/device: conversational identity only.
- Local llama.cpp listener: inference transport only, bound to loopback or a node-local network.
- Container sandbox: terminal/file execution boundary.
- Authenticated Gateway admin edge: operator authentication and HTTP projection only.
- Agent Admin Service: bounded resource lifecycle and redacted status; no prompt/tool ingress.

### Untrusted inputs

- Matrix message text, attachments, room state, display names, reactions, and mentions.
- Matrix users or rooms outside explicit allowlists.
- Model output and tool arguments.
- Network responses, model downloads before checksum verification, logs, and evidence input.

### Forbidden authority widening

- A Matrix event cannot grant a machine capability.
- The profile cannot expose or enable the Hermes API-server adapter.
- The instance cannot accept public SSH or agent-control ingress.
- Local inference failure cannot select a remote provider or fallback.
- Host-direct terminal/file execution cannot replace the declared sandbox.
- Secrets cannot enter Git, Terraform state values, cloud-init output, process arguments, logs, or evidence.
- Admin status/list operations cannot return raw credential values.

## Matrix credential lifecycle

The first cloud profile follows the Sophia operator pattern already used by ZenithOS: a service identity is separate from the operator's human Matrix account, is shown through an operator-facing setup/status flow, and uses a per-profile credential namespace.

The credential class is deliberately different. The E2EE Hermes profile uses a dedicated Matrix user/device access token and persistent device crypto state, not a Sophia application-service token. Production runtime material is referenced from AWS Secrets Manager. The admin API stores and returns secret references and redacted configuration status, never raw credential values through normal list/status operations.

## Configuration contract

`infra/hermes_cloud_agent/profile.schema.json` is the first-proof contract. It requires:

- required Matrix E2EE;
- non-empty user and room allowlists;
- room-scoped sessions and persistent crypto storage;
- `api_server_enabled: false`;
- custom inference at a loopback OpenAI-compatible URL;
- a pinned SHA-256 model digest and an empty fallback list;
- Docker sandboxing;
- encrypted profile storage;
- Systems Manager administration;
- no public SSH or agent ingress.

The node installs upstream Hermes release `v2026.7.20` at commit `3ef6bbd201263d354fd83ec55b3c306ded2eb72a` with the Matrix E2EE extra. `HERMES_HOME` is the encrypted per-profile directory, so Hermes' native Matrix crypto store resolves to `<profile-home>/platforms/matrix/store`. The Matrix Secrets Manager value is a JSON object with required `access_token` and stable `device_id` fields plus an optional `recovery_key`; the runtime fetches it into process memory and does not write a profile `.env` file.

The declared Docker terminal backend is provided through a dedicated rootless Podman compatibility socket owned by the `hermes` service user. The agent is not added to a host Docker group and cannot reach a root-owned Docker socket.

Later remote-inference and secS profiles require a separately reviewed schema version rather than weakening version 1.

## Tasks — commit boundaries

### Task 1: Contract and evidence design

Add this repo-owned spec, threat boundary, configuration schema, schema tests, and evidence checklist.

### Task 2: AWS node infrastructure

Add the minimal encrypted persistent volume, least-privilege IAM/SSM role, private networking, and no-public-ingress controls.

### Task 3: Hermes profile and Matrix gateway

Add isolated profile materialization, runtime secret injection, current Hermes installation, Matrix E2EE configuration, persistent crypto state, and service supervision with the API server disabled.

### Task 3A: Hub agent administration seam

Add the Agent Admin Service contract and implementation, an authenticated Gateway admin HTTP projection, one-profile desired/observed state, fail-closed AWS Systems Manager operation dispatch, Matrix credential-reference lifecycle, and redacted status/evidence output. This is not generic Hermes ingress and does not expose prompt or arbitrary tool execution.

### Task 4: Same-node inference

Add checksum-pinned llama.cpp/Qwen provisioning, loopback-only serving, explicit custom-provider binding, readiness, and no-fallback behavior.

### Task 5: Proof harness and evidence

Add and run restart persistence, authorized encrypted Matrix round trip, unauthorized user/room denial, no-listening-HTTP-port, local-inference provenance, sandbox, and redaction checks.

## Acceptance criteria

- One authorized encrypted Matrix event produces a response before and after gateway restart.
- Unauthorized users and rooms cannot activate the profile.
- Matrix device/crypto identity and permitted session state persist.
- Evidence identifies the exact local model and runtime binding.
- No Codex, OpenRouter, or other remote fallback is configured or observed.
- The Hermes API server is disabled and no public control port exists.
- The security group has no public SSH or agent-interaction ingress.
- Administration uses Systems Manager.
- Terminal/file work is sandboxed.
- ZenithOS-compatible admin routes can register and inspect the first profile without returning raw Matrix credentials.
- The private admin service can report desired/observed state and request only declared Systems Manager lifecycle operations.
- No raw credentials or machine-local operator paths appear in committed files or evidence.

## Evidence checklist

### Static contract

- [ ] JSON Schema validates with Draft 2020-12.
- [ ] Valid Matrix-only/local-inference example passes.
- [ ] Optional E2EE, enabled API server, remote inference URL, non-empty fallback, local tool backend, unencrypted storage, SSH administration, public SSH, and public agent ingress each fail validation.

### Infrastructure

- [ ] Terraform plan identifies one dedicated node and encrypted persistent state.
- [ ] Security-group evidence shows no public ingress.
- [ ] IAM evidence is limited to Systems Manager, declared secret reads, logs/metrics, and required model storage reads.
- [ ] No secret value appears in plan output or state-backed variables.

### Runtime

- [ ] Hermes profile home and Matrix crypto store are on encrypted persistent storage.
- [ ] Matrix E2EE mode is required and allowlists are non-empty.
- [ ] Hermes API adapter is absent/disabled and no generic API port listens.
- [ ] llama.cpp listens only on loopback or the node-local container network.
- [ ] Model checksum matches the declared digest.
- [ ] Hermes has no configured remote fallback.
- [ ] Sandbox backend is operational before gateway readiness.
- [ ] Agent admin status is redacted and contains secret references/configured flags only.
- [ ] Admin operations are allowlisted and dispatch through AWS Systems Manager rather than public node ingress.

### Live proof

- [ ] Authorized encrypted Matrix round trip succeeds.
- [ ] Gateway restart succeeds without changing Matrix device/crypto identity.
- [ ] Post-restart encrypted Matrix round trip succeeds.
- [ ] Unauthorized Matrix user is denied.
- [ ] Unauthorized room is denied.
- [ ] Local inference provenance is captured without prompt or secret leakage.
- [ ] Evidence packet is redacted and contains no credentials, tokens, or operator-local paths.

## Stop conditions

Stop rather than widen or simulate if:

- AWS or Matrix credentials are unavailable;
- required E2EE cannot initialize or persist;
- the machine requires public SSH or generic HTTP ingress;
- the local model cannot become ready without remote fallback;
- secrets would enter Terraform state, user data, process arguments, logs, or Git;
- the sandbox is unavailable;
- live evidence cannot distinguish local inference from a remote provider.

## Explicit non-goals

- Generic Hermes HTTP ingress
- secS implementation or exposure
- Separate-machine or Hub-backed inference
- Fleet scheduling, autoscaling, or multi-profile density beyond the first profile registry seam
- Broad Hub cleanup
- Production traffic or production-readiness claims
