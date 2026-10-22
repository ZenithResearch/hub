# Renew the existing workspace permission

The workspace keeps its machine identity, issuer, native journal, Frank journal, process drafts and published versions. Renewal replaces only the finite permission window for the same five authoring/read operations. The command grant lasts at most one hour; the separately provisioned machine key keeps its original expiry. No permission is renewed automatically, no key is rotated, and run/provider execution remains disabled.

The configured status distinguishes `active`, `not_yet_valid`, `expired` and `unknown` permission observations. Expired permission does not mean the identity should be recreated. Status includes the exact selected receipt's Unix-second window, key expiry when that receipt contains it, and observation time. Refresh status for a current observation. An older receipt without a key-expiry field remains readable and reports that expiry as unavailable.

## Upgrade the stopped workspace software when required

An older installation cannot gain the renewal command by editing its release pins. Use a reviewed new offline workspace bundle. The supported upgrade verifies the old installed inventory and permits changes only to the owner setup binary, Frank wheel and controller. Runtime, Service, all other wheels, Wallet command/verifier bytes and their original provenance must match. It preserves `data`, `authority` and `config`, and retains the previous software and release receipts under `software-upgrades/<id>/previous/`.

Set these values to the owner's exact bundle and manifest SHA256. Run from that bundle's controller using the same pinned Python 3.12 installation:

```sh
WORKSPACE_PYTHON=/opt/homebrew/bin/python3.12
WORKSPACE_ROOT="$HOME/Library/Application Support/Zenith/Process-Workspace"
WORKSPACE_BUNDLE=/absolute/path/to/reviewed-workspace-bundle
WORKSPACE_BUNDLE_SHA=the_exact_64_character_bundle_manifest_sha256
"$WORKSPACE_PYTHON" -I -S -B "$WORKSPACE_ROOT/control.py" workspace-stop --root "$WORKSPACE_ROOT"
"$WORKSPACE_PYTHON" -I -S -B "$WORKSPACE_BUNDLE/process_mac.py" workspace-software-upgrade \
  --root "$WORKSPACE_ROOT" --bundle "$WORKSPACE_BUNDLE" --bundle-sha256 "$WORKSPACE_BUNDLE_SHA"
```

Success returns `hub.workspace-software-upgrade-complete.v1` with the exact new bundle digest and `data_preserved:true`, `authority_preserved:true`, `external_effects:false`. The new installed receipt is published after the offline software inventory is verified. The original release and software are retained. This command does not renew authority or start services.

An interrupted upgrade retains `logs/workspace-software-upgrade.json` and its previous/candidate software. Normal actions report that reconciliation is required. There is no automatic rollback or upgrade resume; retain the complete installation for owner reconciliation. Never delete the hold, overwrite release pins, copy another workspace's custody, or rerun setup to get around it.

## Prepare and apply a bounded renewal

In the lightweight Hub, stop the workspace, select **Prepare permission renewal**, then review **Renew workspace permissions**. The review shows the new command permission window, unchanged key expiry, prior native receipt digest, fixed scope, target and configuration digest. Applying uses the exact reviewed public proposal bytes and prior composition. Starting the workspace afterward is a separate explicit action.

The equivalent controller commands are:

```sh
"$WORKSPACE_PYTHON" -I -S -B "$WORKSPACE_ROOT/control.py" workspace-status --root "$WORKSPACE_ROOT"
"$WORKSPACE_PYTHON" -I -S -B "$WORKSPACE_ROOT/control.py" workspace-renew-prepare --root "$WORKSPACE_ROOT"
# Set this to plan_sha256 returned above, after reviewing the proposal/window.
WORKSPACE_RENEWAL_SHA=the_exact_64_character_plan_sha256
"$WORKSPACE_PYTHON" -I -S -B "$WORKSPACE_ROOT/control.py" workspace-renew \
  --root "$WORKSPACE_ROOT" --selection-sha256 "$WORKSPACE_RENEWAL_SHA"
"$WORKSPACE_PYTHON" -I -S -B "$WORKSPACE_ROOT/control.py" workspace-start --root "$WORKSPACE_ROOT"
```

Preparation returns `hub.workspace-renewal-proposal.v1`, `same_identity:true`, `authority_changed:false`, the finite `not_before`/`not_after`, original `identity_not_after`, and `plan_sha256`. If a previous public renewal proposal exists, supply its exact SHA256 with `workspace-renew-prepare --selection-sha256 OLD_SHA`; the previous bytes are retained. A proposal cannot extend the original key lifetime.

Apply first checks that Frank has no unresolved command intents or cleanup marker. Native Castalia Wallet alone performs the coupled same-identity renewal under the exact predecessor/current-state checks. It writes immutable successor files under `authority/renewals/<native-generation>/`; original receipts/configs remain unchanged. Frank migrates an existing bound journal's configuration metadata transactionally without replaying observed commands. If no journal exists, the controller rechecks that the database and sidecars remain absent and does not create or migrate one. Closed run admission stays disabled. The additive public `config/workspace-native-selection.json` selects the completed successor only after its client/admission composition is consistent.

Success returns `hub.workspace-renewal-complete.v1` with `identity_preserved:true`, `commands_preserved:true`, `execution_enabled:false`, the exact predecessor/successor receipt bindings and `journal:bound|absent`. Renewal retains the finite native journal capacity; it does not prune history or restore exhausted capacity.

After Native finalizes the renewal and writes its successor receipt, Wallet's supported cleanup removes superseded private custody revisions under an exact current-state check. This makes subsequent renewals possible without replacing the key. Original public receipts/configurations and both command histories remain retained. An interruption before cleanup keeps the completed receipt and renewal hold; exact-plan resume can finish cleanup while custody remains observable. Poisoned or unobservable custody remains blocked for checked owner recovery; this release does not reconcile it automatically.

## Preserve uncertain outcomes

`logs/workspace-renewal.json` holds the full transition until native publication, Frank binding, client/admission files and selected receipt agree. Original and next metadata, the exact proposal and receipts remain available. New proposals, ordinary commands and starts are blocked during an uncertain transition.

When the UI explicitly offers **Resume retained permission renewal**, or the owner has verified that child ownership/Frank cleanup is resolved, resume only the exact same proposal:

```sh
"$WORKSPACE_PYTHON" -I -S -B "$WORKSPACE_ROOT/control.py" workspace-renew-resume \
  --root "$WORKSPACE_ROOT" --selection-sha256 "$WORKSPACE_RENEWAL_SHA"
```

Resume does not choose a fresh window or replace the identity. A fully reaped Native exit4 may release only its generic child-ownership marker while retaining this renewal hold and native recovery state. Unreaped/unknown child ownership, a Frank cleanup marker, substituted metadata or another pending native transition stays blocked. Native recovery prepared before the Wallet mutation cannot replay configuration through a lost guard; it remains `recovery_required` for explicit owner reconciliation. Never edit or remove native recovery files, reset identity, clear pending command history, or manufacture a replacement request to bypass these conditions.

Source tests use fake transports and tiny isolated filesystem fixtures. Offline installed-bundle verification, real native renewal, journal preservation and browser presentation checks must be qualified separately before delivery is accepted.
