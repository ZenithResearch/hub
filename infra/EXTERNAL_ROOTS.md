# External roots

Hub can use a configurable external root for large, disposable, or machine-local artifacts that should not live in the repository and should not consume the Mac's internal disk.

Canonical config:

- `infra/external-roots.yaml`

Bootstrap env:

```bash
source scripts/hub_external_env.sh /Volumes/BJJ-Cache/zenith-cache
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

## Current recommended Mac external roots

Tooling/cache root:

`/Volumes/BJJ-Cache/zenith-cache`

Runtime-data root:

`/Volumes/BJJ-Runtime/zenith/data/cache`

The BJJ external disk is now GPT/APFS with separate APFS volumes for archive, cache, and runtime data. Use `BJJ-Cache` for recreatable tooling caches and `BJJ-Runtime` for explicitly migrated local runtime state such as Docker Desktop's `Docker.raw`. The scripts do not assume the drive is always mounted: set `HUB_REMOTE_ROOT` or pass a root path explicitly.

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
source scripts/hub_external_env.sh /Volumes/BJJ-Cache/zenith-cache
scripts/terraform_external.sh infra/aws_baseline_80 validate -no-color
scripts/terraform_external.sh infra/matrix/aws validate -no-color
```

The wrapper sets:

- `TF_PLUGIN_CACHE_DIR=$HUB_REMOTE_ROOT/tooling-cache/terraform-plugin-cache`
- `TF_DATA_DIR=$HUB_REMOTE_ROOT/tooling-state/terraform/<module>`
- `HUB_EXTERNAL_TMPDIR=$HUB_REMOTE_ROOT/temp`
- `TMPDIR=$HUB_REMOTE_ROOT/temp` only when the external filesystem supports Unix domain sockets

That prevents Terraform from writing hundreds of MB to each module's internal `.terraform` directory. The current BJJ cache volume is APFS and supports Unix domain sockets, so `TMPDIR` can move to the external root for Terraform and other local tooling. If a different external drive is exFAT/NTFS, Terraform provider plugins still need a socket-capable temp directory, so `TMPDIR` may stay on internal APFS while the large provider cache and state dirs live externally.

## External disk state

The current BJJ drive has been destructively backed up, reformatted as GPT/APFS, and split into shared-space APFS volumes:

- `/Volumes/BJJ-Archive` — restored/archive material when needed.
- `/Volumes/BJJ-Cache` — recreatable tooling and build caches; canonical Hub root is `/Volumes/BJJ-Cache/zenith-cache`.
- `/Volumes/BJJ-Runtime` — explicitly migrated local runtime state; Docker Desktop's sparse `Docker.raw` is symlinked from the internal Docker path to `/Volumes/BJJ-Runtime/zenith/data/cache/docker-desktop/vms/0/data/Docker.raw`.

The pre-reformat drive backup is in `s3://zenith-bjj-drive-backup-044528206149-us-east-1/bjj-drive-2026-05-20/`. Do not delete that backup until the operator explicitly approves lifecycle/cost cleanup.
