# Matrix/Synapse secret boundary

ISS-P14-004 defines the production Matrix secret classes and where their handles live.

## Secret classes

- `matrix_homeserver_signing_key`
- `matrix_macaroon_secret_key`
- `matrix_registration_shared_secret`
- `matrix_appservice_as_token`
- `matrix_appservice_hs_token`

## Rotation owner

Rotation owner: Hub operator. Values are rotated through AWS Secrets Manager / approved operator-secret input flow, not by committing files.

Generated appservice registration files are runtime artifacts. They may contain rendered token values and must stay out of git history.

`terraform.tfvars` examples may show placeholder wiring, but `terraform.tfvars` must not contain raw production Matrix secrets.

Do not print raw Matrix, appservice, registration, macaroon, or signing-key material in logs, PR bodies, screenshots, or daily notes.

For controlled administrator creation, follow [`matrix-admin-provisioning.md`](matrix-admin-provisioning.md). The operator script reads the registration secret directly from AWS Secrets Manager and never accepts its value as a command-line option.
