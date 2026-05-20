# Model profiles

Project H defines the durable model-configuration shape for Hub/Hermes agents and the future ZenithOS operator UI.

Canonical machine-readable contract:

- `infra/model-profiles.yaml`

Static validator:

- `scripts/model_profile_check.py`

## Product shape

Do not turn Frank's current model config into a single global model setting. The durable unit is:

```text
agent/persona -> purpose profile -> deployment profile -> provider/endpoint/model/fallback/runtime settings
```

ZenithOS should operate this as a control surface:

```text
Agents -> Frank -> Profiles -> review_brief_compiler -> Provider/Model/Fallback -> Test -> Save
```

Hub owns execution. ZenithOS writes or requests safe config changes through Hub and displays effective config, connectivity state, and audit metadata. ZenithOS must not execute model calls itself and must not persist raw provider secrets.

## Current production binding

The current production binding is intentionally explicit:

- Agent: `frank`
- Profile: `review_brief_compiler`
- Deployment profile: `cloud-aws-prod`
- Provider: `hub-internal-openai-compatible`
- Endpoint handle: `prod-llama-server`
- Base URL: `http://llama-server.zenith-hub-prod.local:3690/v1`
- Model: `Qwen3.5-9B-Q4_K_M.gguf`
- Secret reference: `none`
- Fallback profile: `fallback_fast`

This records the existing bootstrap env shape without making env vars the product surface:

- `FRANK_MODEL=Qwen3.5-9B-Q4_K_M.gguf`
- `OPENAI_BASE_URL=http://llama-server.zenith-hub-prod.local:3690/v1`
- `OPENAI_API_KEY` is a none-equivalent placeholder at runtime, not a real bearer secret.

## Required dimensions

Each agent profile binding should declare:

- agent/persona
- purpose profile
- deployment profile
- provider
- endpoint reference
- model name
- secret reference, never a raw secret
- timeout
- temperature
- max tokens
- cost tier
- latency tier
- fallback profile
- capability expectations
- connectivity-check posture

## Secret posture

`infra/model-profiles.yaml` may contain handles such as `none` or `hub-secret-handle`. It must not contain raw tokens, API keys, passwords, or bearer values.

Future secret-backed providers should store the raw value in Hub/AWS/Keychain-backed storage and reference only a stable handle here or in Hub's DB.

## Runtime migration path

1. Keep production Frank env vars as a bootstrap bridge.
2. Add Hub-side effective-config resolution that can read typed agent/profile records.
3. Add admin/operator APIs for reading safe effective config, testing connectivity, and saving audited changes.
4. Add ZenithOS UI for profile editing and test/save flows.
5. Retire direct single-purpose env-var editing once typed profile resolution is active.

## Current Hub read API

Gateway exposes a first read-only operator endpoint:

```bash
GET /v1/admin/model-profiles/effective?agent=frank&profile=review_brief_compiler&deployment_profile=cloud-aws-prod
Authorization: Bearer <review-access-admin-token>
```

The response is intentionally safe for ZenithOS display:

- effective agent/profile/deployment binding
- provider kind and endpoint handle
- endpoint base URL and visibility
- model name
- runtime knobs such as timeout, temperature, max tokens, cost tier, and latency tier
- fallback profile
- secret handle/configured state, never raw secret material
- safe bootstrap env values only
- `secrets_printed: false`

Unknown agents/profiles/deployment profiles fail visibly instead of falling back to a global model.

## Current Hub connectivity-check API

Gateway also exposes a read-only/action-test endpoint:

```bash
POST /v1/admin/model-profiles/connectivity-check?agent=frank&profile=review_brief_compiler&deployment_profile=cloud-aws-prod
Authorization: Bearer <review-access-admin-token>
```

The endpoint resolves the effective profile and sends a minimal OpenAI-compatible chat-completions probe to the configured endpoint:

- `messages: [{role: user, content: health check}]`
- `max_tokens: 1`
- `temperature: 0`
- `stream: false`

The response is redacted operational status only:

- `ok`
- agent/profile/deployment profile
- provider, endpoint ref, model
- status code
- latency in milliseconds
- short detail string
- `secrets_printed: false`

It must not return raw provider responses, prompts beyond the fixed health-check string, authorization headers, API keys, bearer tokens, or model output text. Failed connectivity returns safe status without exposing secrets.

## Verification

Run:

```bash
python scripts/model_profile_check.py
python -m pytest tests/test_model_profile_check.py -q
```

The validator is intentionally static and non-deploying. It does not call production endpoints, start containers, or mutate runtime config.
