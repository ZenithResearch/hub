# Immutable component image build

This Hub-owned build slice consumes reviewed wheels and public process resources. It does not build from component checkouts, copy live configuration, import business aggregates into Hub, install a native helper, push a registry image or deploy a service. The component entrypoints remain independently owned. All image labels and receipts keep production qualification false.

The policy in `config/process-component-images.v1.json` selects the exact official Python 3.12.12 Linux arm64 base manifest, with its upstream index/config/root filesystem identities. The primary Docker registry was checked directly. Index `593bd06e…` resolves to Linux arm64 manifest `228390ec…`; no mutable tag appears in generated Dockerfiles. Other architectures need a separately reviewed policy and matching binary wheels.

The final candidate uses Runtime wheel `743a4757…`, Service `22574bb2…` and Frank `6c84adc2…`. Runtime source revision `4bdcd06…` binds the coherent CI/backup commit and its reproducible packaging provenance. The newer wheel has the same members, metadata, application, SDK, contract and resource bytes as the historical `8c77197a…` archive built from application baseline `54d7640…`. Earlier contexts remain historical. Source commits and every admitted wheel digest are recorded in the policy; source review and local packaging evidence do not establish a hosted CI or image result.

The host tuple records Darwin installed-file evidence. Its package versions and component hashes are checked before preparation; its Darwin native extensions are never copied into Linux. Twelve separately fetched Linux-compatible dependency wheels were matched against primary PyPI hashes and are locked by filename, version and SHA-256. Preparation evaluates Python 3.12/Linux markers and dependency constraints, verifies wheel RECORD entries and platform tags, rejects Darwin binaries and unsafe zip paths, and checks the public SDK/consumer contract hashes. Each admitted component package, version and actual wheel SHA must agree with both its source policy and the installed tuple; changing the wheel allowlist alone is rejected.

Host preparation and tests use a separate, minimal environment. `config/process-image-host-tools.v1.json` pins the reviewed Darwin arm64 CPython 3.12.12 executable, and `config/process-image-host-tools.lock` admits only pip 25.3, packaging 26.2, pytest 9.1.1, iniconfig 2.3.1, pluggy 1.6.0 and Pygments 2.20.0. The six standard upstream wheel hashes were checked against exact-version primary PyPI metadata, retained with the preparation evidence. A new host platform or Python installation needs a reviewed profile rather than an automatic interpreter substitution. This host profile does not change the Linux image interpreter/base pin.

The stdlib bootstrap verifies the exact wheelhouse, hashes, metadata and RECORD entries before creating a venv without global site packages. It executes the verified upstream pip wheel and installs the closure offline with hash enforcement, binary wheels only and no compilation. It records a receipt binding interpreter, profile, lock, wheel identities and installed files. Every image tool invocation rechecks that receipt, original wheel hashes, package code and module origins. There is no ambient or pip-vendored packaging fallback and no broad Hub dependency install. Keep the host wheelhouse and receipt available for verification.

```sh
"$REVIEWED_HOST_PYTHON" -I -B scripts/bootstrap_process_image_tools.py create \
  --environment "$NEW_IMAGE_TOOL_ENVIRONMENT" \
  --wheelhouse "$EXACT_HOST_TOOL_WHEELHOUSE"

"$NEW_IMAGE_TOOL_ENVIRONMENT/bin/python" -I -B scripts/bootstrap_process_image_tools.py verify
```

Preparation creates three small allowlisted contexts. Runtime contains its distribution and dependency closure. Service additionally installs the same public SDK distribution, currently packaged with Runtime, to support its independently owned optional runtime client; its entrypoint stays Service. Frank contains its declared Runtime/HTTP dependencies. Published process resources install from the unchanged Runtime wheel into the virtual environment's data prefix and are checked byte for byte. No component checkout, operator database, environment file, model provider SDK or credential is an image input.

```sh
"$NEW_IMAGE_TOOL_ENVIRONMENT/bin/python" -I -B scripts/process_component_images.py prepare \
  --tuple "$REVIEWED_INSTALLED_TUPLE_JSON" \
  --wheelhouse "$EXACT_PLATFORM_WHEELHOUSE" \
  --contexts "$NEW_CONTEXT_DIRECTORY"

"$NEW_IMAGE_TOOL_ENVIRONMENT/bin/python" -I -B scripts/process_component_images.py verify \
  --contexts "$NEW_CONTEXT_DIRECTORY" \
  --context-sha256 "$REVIEWED_CONTEXT_MANIFEST_SHA256"
```

The tuple must match the policy's exact SHA-256. The wheelhouse must contain exactly the fifteen admitted files, with no extra input. Missing, incompatible or tampered wheels/contracts fail before a context is published. Preparation is create-only and deterministic. Each build input also records the host interpreter/profile/lock identities and versions, without prefix-specific paths. The context manifest hashes every file; a changed Dockerfile, lock, source tool, wheel, special file, symlink or added private config is rejected before any Docker call. Keep the reviewed context manifest digest outside the context when approving a build.

The build command requires an already available engine, default builder and preloaded exact base image/config. It never starts a daemon, creates a builder, pulls a base, logs in, pushes or deploys. Engine access and base preloading are operator coordination steps. The local build uses `--pull=false`; the base must already be present in that builder's store. Build RUN steps use network none and pip uses only local, hash-required binary wheels, without compilation or dependency downloads.

```sh
"$NEW_IMAGE_TOOL_ENVIRONMENT/bin/python" -I -B scripts/process_component_images.py build \
  --contexts "$NEW_CONTEXT_DIRECTORY" \
  --context-sha256 "$REVIEWED_CONTEXT_MANIFEST_SHA256" \
  --output "$NEW_LOCAL_IMAGE_ARTIFACT_DIRECTORY"
```

Each component exports twice as OCI with fixed `SOURCE_DATE_EPOCH`, timestamp rewriting and explicit compression. Both complete archives and OCI identities must agree. OCI checks hash compressed descriptors, then decompress each actual layer and compare its SHA-256 with the corresponding ordered config `rootfs.diff_ids`, including the pinned base prefix. They validate tar members without filesystem extraction, permit only reviewed gzip or uncompressed OCI tar formats, and bound archive bytes/member counts, extended metadata, compressed/decompressed layer sizes and aggregate output. Unsafe paths, escaping links, special members, unreferenced payloads and malformed archives are rejected. Filesystem hashes are computed from layer bytes rather than accepted from config claims. OCI checks also bind platform, source revision, nonroot user, exact component entrypoint and unqualified labels. A failed build, mismatch or smoke leaves partial diagnostic artifacts and no final image manifest. BuildKit/engine and Docker executable identity are recorded; cross-toolchain reproducibility is not inferred from two local builds. Base metadata resolution by a different builder is not an offline result: use the coordinated preloaded default builder and retain the actual build log.

The final image contains only its installed virtual environment, image tools and public input/installed inventories. Its user is `10001:10001`, default command is the owner's `--help`, and no writable operational data or service config is baked in. Local smoke uses network none, a read-only root, no capabilities, no new privileges and disposable `/data`/`/tmp` tmpfs. It verifies installed package/source/contract/resource bytes, then invokes the actual owner CLI. Runtime and Service must be live but return production readiness 503 with default-deny authority. Frank must report missing authority and reject executor registration. No model, STT, status, vault, native signing or real provider effect is exercised.

Only after all three images reproduce and pass smoke is `image-manifest.json` written. It binds OCI archive/manifest/config/layer digests, the reviewed context, source commits, resource inventory, installed package-file inventory and smoke receipts. The installed inventory also includes the base OS package list and the base-supplied Python packaging tools. These files are local build evidence, not public publication or a qualified signing chain.

The historical `b044785f…` Service/Frank verification-only helper and Runtime transport helper `be204953…` remain distinct and unchanged. Both retained binaries are Darwin artifacts and are excluded from Linux contexts. A matching Linux helper and its receiver/issuer custody, descriptor, identity and qualification evidence require owner-reviewed work; changing a hash or copying a Darwin binary is not supported. Until that gate closes, native production paths remain unavailable.

Current evidence distinguishes preparation from execution: offline context reproduction and prepublication negatives passed; image build/reproduction/smoke and image digests remain pending engine availability. No hosted CI receipt, deployable native qualification or production readiness is asserted by this document.

The isolated prepublication suites are `tests/test_process_component_images.py` and `tests/test_process_image_host_tools.py`. Run them with the locked interpreter, `-I -B`, plugin autoload disabled, an explicit source-root insertion for these Hub test modules, `-c /dev/null`, `-p no:cacheprovider` and a dedicated temporary directory. The tests exercise actual bootstrap/CLI receipt checks, hostile inputs and no-Docker rejection; synthetic OCI tests do not establish that a component image has built or passed smoke.
