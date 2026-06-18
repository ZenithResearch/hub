# Review Auth policy audit, preflight, and dry-run repair

Hub exposes admin-only Review Auth policy tools for safely inspecting and planning repairs to policy rows without exposing secrets.

## Security guarantees

All endpoints below require the Review Access admin bearer token. Responses contain public policy metadata only and must not include raw access codes, access-code hashes, review session tokens, the admin token, or database credentials. Dry-run repair never writes database rows.

## List policy rows

Use the policy list endpoint to inspect active or stale rows:

```bash
curl -sS \
  -H "Authorization: Bearer $REVIEW_ACCESS_ADMIN_TOKEN" \
  "$HUB_URL/v1/admin/review-auth/policies?project_id=swrl&access_code_id=dan-prota-swrl-review"
```

Optional filters:

- `project_id`
- `access_code_id`
- `active=true|false`

Rows include only safe fields: `access_code_id`, `project_id`, `deployment_id`, `deployment_slug`, `allowed_origin`, `subject_pattern`, `active`, timestamps, and `staleness_flags`.

Useful stale flags include:

- `bare_host_wildcard_subject`: production subject pattern uses `https://host*` instead of `https://host/*`.
- `localhost_fixed_port`: local policy is pinned to a specific localhost port instead of the any-port localhost wildcard.
- `unexpected_deployment_id`: known project has a non-canonical deployment id.
- `missing_access_code`, `missing_project`, `missing_deployment`: policy row references missing related rows.

## Preflight a rotate payload

Before rotating or generating an access code, validate the exact rotate payload with preflight:

```bash
curl -sS \
  -X POST \
  -H "Authorization: Bearer $REVIEW_ACCESS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d @rotate-payload.json \
  "$HUB_URL/v1/admin/review-auth/access-codes/preflight"
```

Preflight runs the same validation as rotate, returns `422` with the same validation detail on invalid input, and does not create clients, projects, deployments, access-code rows, policy rows, sessions, or generated raw codes. A successful response returns a normalized policy preview and `secrets_printed=false`.

## Dry-run repair plan

To preview what Hub would canonicalize for a specific project and access code:

```bash
curl -sS \
  -X POST \
  -H "Authorization: Bearer $REVIEW_ACCESS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"swrl","access_code_id":"dan-prota-swrl-review","mode":"dry_run"}' \
  "$HUB_URL/v1/admin/review-auth/policies/repair-plan"
```

The repair plan is dry-run only. It returns rows that would change with `old_policy` and `proposed_policy` tuples containing public policy fields only: `deployment_id`, `deployment_slug`, `allowed_origin`, and `subject_pattern`.

## Rotate vs. repair

Prefer rotation when issuing or replacing reviewer access, changing the desired allowlist, or reconciling multiple policy rows with a known canonical rotate payload. Use preflight first, then rotate only after validation succeeds.

Use the dry-run repair plan when an existing policy row is stale but the desired public tuple is obvious, such as changing a bare-host wildcard from `https://example.com*` to `https://example.com/*` or generalizing fixed localhost ports. This PR intentionally does not apply repairs; any write/apply path should be reviewed and approved separately.
