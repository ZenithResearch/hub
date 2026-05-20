# External roots

Hub can use a configurable external root for large, disposable, or machine-local artifacts that should not live in the repository and should not consume the Mac's internal disk.

Canonical config:

- `infra/external-roots.yaml`

Bootstrap env:

```bash
source scripts/hub_external_env.sh /Volumes/BJJ/zenith-cache
```

Terraform wrapper:

```bash
scripts/terraform_external.sh infra/matrix/aws init -backend=false -input=false -no-color
scripts/terraform_external.sh infra/matrix/aws validate -no-color
```

## Ontology

This is intentionally not the vault. The vault is for durable knowledge. External roots are for operational substrate: cache, temp, runtime state, local model artifacts, and build outputs.

Namespaces:

- `tooling-cache/` — deterministic tool caches, safe to recreate.
- `tooling-state/` — tool working dirs like Terraform `TF_DATA_DIR`, safe to recreate.
- `temp/` — temp files for tools that otherwise spill into `/var/folders` or `/tmp`; Terraform only uses this as `TMPDIR` when the filesystem supports Unix sockets.
- `model-artifacts/` — local model files or downloaded model caches, not committed.
- `build-cache/` — build-system caches, not committed.
- `runtime-state/` — optional local runtime state that must be explicitly backed up before deletion.

## Current recommended Mac external root

`/Volumes/BJJ/zenith-cache`

This drive currently has enough space for large provider/model/build caches. The scripts do not assume it is always mounted: set `HUB_REMOTE_ROOT` or pass a root path explicitly.

## Rules

- Do not install duplicate Terraform providers per module on the internal disk.
- Do not use the repo as a cache/artifact warehouse.
- Prefer CI for clean installs and external root for local validation.
- Keep `.terraform/`, model caches, Docker data, and build caches ignored/disposable.
- Do not move Docker Desktop data automatically; that is a separate side-effectful migration.
- Do not put secrets in external root configs. Secrets stay in keychain, secret managers, `.env`/tfvars ignored files, or CI environment secrets.

## Terraform pattern

Use:

```bash
source scripts/hub_external_env.sh /Volumes/BJJ/zenith-cache
scripts/terraform_external.sh infra/aws_baseline_80 validate -no-color
scripts/terraform_external.sh infra/matrix/aws validate -no-color
```

The wrapper sets:

- `TF_PLUGIN_CACHE_DIR=$HUB_REMOTE_ROOT/tooling-cache/terraform-plugin-cache`
- `TF_DATA_DIR=$HUB_REMOTE_ROOT/tooling-state/terraform/<module>`
- `HUB_EXTERNAL_TMPDIR=$HUB_REMOTE_ROOT/temp`
- `TMPDIR=$HUB_REMOTE_ROOT/temp` only when the external filesystem supports Unix domain sockets

That prevents Terraform from writing hundreds of MB to each module's internal `.terraform` directory. If the external drive is exFAT/NTFS, Terraform provider plugins still need a socket-capable temp directory, so `TMPDIR` may stay on internal APFS while the large provider cache and state dirs live externally.

## External disk caveat

The current drive is mounted as exFAT. Basic writes and executable files work, but macOS may create AppleDouble `._*` sidecars, and exFAT does not support Unix domain sockets. This is acceptable for local caches, model files, and Terraform plugin/state directories, but not ideal for source repos, committed files, or tools that require socket-capable temp directories. If we want to use the drive as a full temp/build root, create an APFS volume or partition.
