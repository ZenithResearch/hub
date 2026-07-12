# Matrix appservice environment configuration

This runbook defines the non-secret homeserver and Matrix identity configuration established by ISS-P15-003. It does not prove appservice authentication or message delivery; those remain the P15-004 and P15-005 gates.

## Environment matrix

| Environment | Client/API transport | Public server identity | Configuration source |
| --- | --- | --- | --- |
| Local | `MATRIX_HOMESERVER_URL=http://localhost:8008`; compose services use `MATRIX_HOMESERVER=http://matrix-synapse:8008` | `MATRIX_SERVER_NAME=localhost`; local users such as `MATRIX_GATEWAY_BOT_USER_ID=@gateway-bot:localhost` and `SOPHIA_MATRIX_USER=@sophia:localhost` | `.env.example` and `docker-compose.yml` |
| Staging | Explicit HTTPS staging URL; never inherit localhost | The staging server name and user-ID suffix must match each other | Deployment-specific non-secret configuration plus secret-store token handles |
| Production | `MATRIX_HOMESERVER_URL=https://synapse.zenith-research.ca` | `MATRIX_SERVER_NAME=synapse.zenith-research.ca`; Gateway identity is `@gateway-bot:synapse.zenith-research.ca` | Terraform injects the URL and Gateway identity when production Synapse is enabled |

A deployment may use a private transport URL for service-to-service traffic only when it is explicitly documented and TLS-authenticated. A private transport URL must never change or obscure the public server identity used in Matrix user IDs, room IDs, signatures, federation, or operator evidence.

## Service keys

- Gateway: `MATRIX_HOMESERVER_URL`, `MATRIX_GATEWAY_BOT_USER_ID`.
- Matrix bridge: `MATRIX_HOMESERVER_URL`.
- Ingest/Sophia: `MATRIX_HOMESERVER`, `SOPHIA_MATRIX_USER`.
- Synapse: `MATRIX_SERVER_NAME` conceptually maps to the immutable production `server_name`; its ECS runtime receives `SYNAPSE_SERVER_NAME` from `public_matrix_domain_name`.

Local compose keeps explicit local defaults. Production Terraform must not derive its URL from `.env.example`, Docker service names, or localhost fallbacks.

## Fail-closed production boundary

When production Synapse is enabled, Terraform requires `public_matrix_domain_name` to equal `synapse.zenith-research.ca`. Gateway receives its HTTPS homeserver URL and Matrix user-ID suffix from that same value, preventing URL/identity drift.

The production appservice token versions and registration are intentionally outside this issue. P15-004 must provision or reference those secret values, authenticate against the configured production endpoint, and capture a redacted `whoami` result before any delivery claim. P15-005 remains responsible for actual event/message delivery.

## Secret handling

Do not place raw appservice tokens, admin tokens, registration secrets, Authorization headers, secret-version values, or unredacted command output in this file, environment examples, tests, Terraform plans, PR bodies, or logs. Production credentials must enter tasks through approved secret-store handles.

## Operator checks

```bash
terraform -chdir=infra/aws_baseline_80 validate
python3 -m pytest -q tests/matrix/test_iss_p15_003_production_config.py
```

Before applying a future P15 deployment, inspect the plan and require:

- Gateway task environment uses the production HTTPS endpoint and matching user-ID suffix.
- Synapse `server_name` remains `synapse.zenith-research.ca`.
- No production Matrix URL contains `localhost`, a Docker service name, credentials, query parameters, or fragments.
- No raw token values appear in the plan or evidence.
