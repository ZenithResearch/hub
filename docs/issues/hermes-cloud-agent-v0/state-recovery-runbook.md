# Hermes cloud-agent Matrix state recovery

This runbook governs the Issue 97 first-proof profile. The Matrix crypto store is device authority, not a disposable cache.

## Fail-closed invariant

`/var/lib/hermes/.active-instance` binds first activation to the exact EBS volume ID and EC2 instance ID. The mount service refuses a different pairing. Do not edit, delete, or replace this marker to make a failed deployment start.

The first proof deliberately has no automated rebind operation. Preserving an old crypto store across a new instance or snapshot clone requires a separately reviewed recovery design.

## Suspected compromise

1. Stop the Hermes gateway through the approved SSM administrative operation.
2. Revoke the dedicated Matrix access token and delete the affected device in Synapse.
3. Remove the service identity from sensitive rooms until recovery is complete.
4. Disable access to the Matrix Secrets Manager version and inspect CloudTrail for secret, KMS, snapshot, volume, and SSM access.
5. Preserve the old EBS volume without mounting it read-write. Do not attach it to a replacement runtime node.
6. Provision a new encrypted volume, Matrix device ID, access token, and empty Hermes crypto store.
7. Verify the new device through an operator-controlled Matrix verification flow before restoring room membership.
8. Re-run the full success and failure evidence suite. Do not reuse evidence from the compromised device.

## Failed or replaced instance without evidence of compromise

For the first proof, replacement still creates a new Matrix device and empty crypto store. The old volume remains retained for operator-gated forensic or future recovery work. This sacrifices agent-readable message history rather than risking duplicate device activation or ratchet rollback.

Do not:

- clear `.active-instance`;
- restore the snapshot onto a live replacement profile;
- run two nodes with the same Matrix token or device ID;
- copy `crypto.db` into a new profile;
- put a Matrix recovery key into the runtime secret;
- weaken `MATRIX_E2EE_MODE=required` to regain service.

## Planned teardown

The EBS volume has Terraform `prevent_destroy`. Teardown therefore requires an explicit operator decision after:

1. the gateway is stopped;
2. the Matrix token is revoked and device removed;
3. required redacted evidence is retained;
4. any forensic-retention requirement is resolved;
5. a reviewed change removes `prevent_destroy` for that teardown only.

A normal Terraform apply must never silently destroy Matrix device state.

## Runtime secret contract

The Secrets Manager value contains exactly:

```json
{"access_token":"<redacted>","device_id":"<stable-device-id>"}
```

Unknown fields, binary secrets, multiline values, and recovery keys fail closed. Device verification and recovery material stay outside the standing runtime role.