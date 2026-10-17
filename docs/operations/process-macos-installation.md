# Direct Mac installation

This is the initial per-user installation for Apple silicon Macs running Python
3.12. It uses the existing Runtime, Service and Frank wheels. It needs no Docker,
VM, administrator account or repository checkout on the target Mac. Each Mac
creates its own environment and private data; do not copy an installed venv,
database or identity from one Mac to the other.

The current installation phase deliberately starts Runtime and Service with
production default-deny authority. Liveness can pass while readiness remains
false. Frank is installed and configured with an empty integration registry; it
has no HTTP daemon on 8093. This installation does not provision an identity,
connect a provider, start delivery pollers or enable external effects.

For current user interactions, a disposable structured-intake example and the proposed Matrix flow, see the [pilot usage guide](process-pilot-usage-guide.md).

## Install and operate

Install Python 3.12 from the normal trusted Python/Homebrew distribution first.
Open the delivered `Install.command`, or run the pinned bundle explicitly:

```sh
python3.12 -I -S -B /path/to/bundle/process_mac.py install \
  --bundle /path/to/bundle --bundle-sha256 RELEASE_MANIFEST_SHA256
```

The default root is `~/Library/Application Support/Zenith/Process`. Installation
is create-only and never overwrites existing data. A failed installation leaves
its private logs and incomplete root for diagnosis; it does not replace an old
installation. Use `--root /absolute/private/new/path` to install a distinct
release or disposable test instance. Python creates the environment at its final
path; moving it afterward is rejected.

```sh
"$HOME/Library/Application Support/Zenith/Process/Process.command" doctor
"$HOME/Library/Application Support/Zenith/Process/Process.command" start
"$HOME/Library/Application Support/Zenith/Process/Process.command" status
"$HOME/Library/Application Support/Zenith/Process/Process.command" stop
```

Double-clicking `Process.command` runs doctor. Doctor checks the release and
installed files before importing component code, verifies Frank configuration
without connecting adapters, and reports owned service liveness separately from
authority readiness. Expected current results include `authority: pending`,
`pilot_ready: false`, and `external_effects: false`.

Start registers two installation-specific user launchd jobs. Plists stay inside
the installation's `jobs/` directory, so this does not enable start at login. A
port conflict or foreign launchd label is an error, never permission to stop or
adopt another service. Runtime uses loopback 8092 and Service loopback 8091; set
`--runtime-port` and `--service-port` during installation for an isolated instance.

Stop forwards Runtime SIGINT and Service SIGTERM, waits for the owned children,
and reports graceful versus forced shutdown in private logs. It attempts both
components if one fails and never sends signals to stored arbitrary PIDs. It
does not delete databases, WALs, outputs, logs, identities or recovery evidence.
Run status/stop again if launchd or child absence cannot be established.

## Build the offline bundle

The operator builds from exactly the fifteen Darwin wheel pins in
`config/process-macos-release.v1.json`. The Linux image lock is a different
artifact and must not be substituted. The current three component source commits
and whole-wheel hashes are preserved in the release profile.

```sh
python3.12 -I -S -B scripts/process_mac.py bundle \
  --wheelhouse /absolute/reviewed/mac-wheelhouse --output /absolute/new/bundle
```

The output includes all wheels, the controller, profile and `Install.command`.
The printed bundle SHA identifies its payload manifest. Publish that SHA with the
bundle through the normal trusted delivery channel; it is an integrity pin, not
an identity grant or a production qualification claim. The same bundle supports
the operator's and Jess's Apple silicon Macs. Actual installation on Jess's Mac
remains a separate target check.

## What follows installation

1. Provision the approved dedicated Frank identity through native Wallet.
2. Connect native command/result authority and receiver-local policy.
3. Qualify one explicitly enabled live provider, including restart and uncertain
   outcome recovery.
4. Run the operator's first useful workflow, then guide Jess through installation
   and separate identity enrolment.

The installer does not bypass missing authority to make its readiness green.
