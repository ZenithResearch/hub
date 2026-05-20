# ZenithOS Review Access Manager Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a ZenithOS-facing operator flow for creating Review SDK clients/projects/access codes from rolodex people without pasting secrets through chat or ad hoc shell commands.

**Architecture:** ZenithOS should own the operator UI and local configuration workflow; Hub remains the canonical production review-auth store. The UI should select a person from the existing rolodex/contact-book source, create or select a client/project/deployment config, and trigger a Hub-backed code rotation/generation path that never persists raw access codes by default.

**Tech Stack:** ZenithOS SwiftUI, existing rolodex/contact-book data source used for Matrix/Synapse contacts, Hub review-auth APIs or a local Zenith plugin bridge, Hub Postgres/RDS review-auth registry.

---

## Requirements captured

- Operator can choose a reviewer/client from people already in the rolodex.
- Selection should reuse the same people/options source used by the contact book for Matrix/Synapse.
- Operator can create/configure a client and project for review access.
- Operator can generate a review access code or enter one manually.
- Default posture: do not persist the raw access code locally.
- Acceptable ergonomic path: copy generated code to clipboard once and store only the hash/metadata in Hub.
- If persistence is added later, it must be explicit and go to a secure local secret store, not plaintext config.
- Never print raw reviewer codes into chat, logs, source files, CloudWatch, or notes.

## Non-goals for the first slice

- No durable raw-code vaulting by default.
- No broad client CRM system.
- No frontend app embedding of access codes.
- No replacement of Hub as canonical production review-auth registry.

---

## Proposed UX

1. Open ZenithOS Review Access panel.
2. Pick a person from Rolodex / Contact Book.
3. Choose project, e.g. `swrl-ui`, or create a new project config.
4. Choose deployment alias, e.g. `swrl-ui-production-alias`, or dynamic Vercel hook settings.
5. Click `Generate code`.
6. ZenithOS/Zenith plugin calls Hub/local bridge to rotate or seed the access-code row.
7. Raw code is copied to clipboard and displayed once behind a reveal control.
8. UI shows safe metadata only after dismissal: client, project, deployment scope, active status, last rotated time.

Manual-code variant:

1. Operator enters a raw code in a secure text field.
2. ZenithOS sends it directly to the local bridge/Hub mutation endpoint.
3. Raw code is not stored in app state after the request completes.
4. UI clears the text field and confirms hash verification only.

---

## Data boundary

Safe to persist in ZenithOS config:

```text
client_id
client_slug
client_name
rolodex_entry_path
project_id
project_slug
project_name
deployment_id / deployment_slug
allowed_origin
subject_pattern
access_code_id
access_label
last_rotated_at
active
```

Do not persist by default:

```text
raw access code
access-code hash
session token
deploy hook token
DB URL/password
```

If raw-code persistence becomes necessary, add a separate explicit setting backed by Keychain, with a warning and audit field.

---

## Hub/API contract needed

Add or expose a narrow operator endpoint or local Zenith plugin command:

```text
POST /v1/admin/review-auth/access-codes/rotate
```

Request shape:

```json
{
  "client_id": "dan-prota",
  "client_slug": "dan-prota",
  "client_name": "Dan Prota",
  "rolodex_entry_path": "notes/Dan Prota.md",
  "project_id": "swrl-ui",
  "project_slug": "swrl-ui",
  "project_name": "SWRL UI",
  "deployment_id": "swrl-ui-production-alias",
  "access_code_id": "dan-prota-swrl-ui-review",
  "access_label": "Dan Prota",
  "mode": "generate" | "provided",
  "access_code": "only present for provided mode"
}
```

Response shape for generate mode:

```json
{
  "ok": true,
  "client_id": "dan-prota",
  "project_id": "swrl-ui",
  "deployment_id": "swrl-ui-production-alias",
  "access_code_id": "dan-prota-swrl-ui-review",
  "raw_code": "returned once only",
  "secrets_printed": false
}
```

For production, prefer returning raw code directly to ZenithOS over logging it. ZenithOS immediately places it on clipboard and then drops it from state.

---

## Task 1: Locate ZenithOS contact-book source

**Objective:** Identify the existing code path that renders Matrix/Synapse contact book options from the rolodex.

**Files:**
- Inspect: ZenithOS repo SwiftUI files once repo path is confirmed.
- Inspect: rolodex loading / Matrix contact-book adapter.

**Steps:**
1. Find the ZenithOS repo location or confirm the current workspace path.
2. Search for contact book views, Matrix/Synapse people selection, and rolodex adapters.
3. Document the reusable model/provider for person options.

**Verification:** Can name the exact Swift files and model types to reuse.

---

## Task 2: Add a Review Access domain model

**Objective:** Define safe persisted metadata for review access configs.

**Files:**
- Create or modify ZenithOS model file for `ReviewAccessConfig`.
- Add tests if the repo has model tests.

**Model fields:**

```swift
struct ReviewAccessConfig: Identifiable, Codable, Equatable {
    var id: String { accessCodeID }
    var clientID: String
    var clientSlug: String
    var clientName: String
    var rolodexEntryPath: String?
    var projectID: String
    var projectSlug: String
    var projectName: String
    var deploymentID: String?
    var deploymentSlug: String?
    var allowedOrigin: String?
    var subjectPattern: String?
    var accessCodeID: String
    var accessLabel: String
    var lastRotatedAt: Date?
    var active: Bool
}
```

**Verification:** Codable round trip does not include raw code fields.

---

## Task 3: Add Hub/local bridge command for code rotation

**Objective:** Provide a safe callable operation that creates/upserts the client/project/access-code row and returns the generated raw code once.

**Files:**
- Hub: add/admin endpoint or script wrapper around existing Postgres review-auth store.
- Zenith plugin: expose a local command if ZenithOS should call through local Hermes/Zenith plugin instead of public Hub admin API.

**Security requirements:**
- No raw code in logs.
- No hash in logs.
- No DB URL/password in logs.
- Return raw code only in response body to authenticated local operator.
- Production mutation path uses existing Hub/RDS backend.

**Verification:** Unit test proves raw code is not present in logs or persisted config.

---

## Task 4: Build ZenithOS Review Access panel MVP

**Objective:** Add a minimal operator UI for selecting a rolodex person, setting project/deployment metadata, and generating/providing a code.

**UI controls:**
- Person picker from rolodex/contact-book provider.
- Project ID/name fields.
- Deployment alias/origin/subject pattern fields.
- Access label field defaulting to person display name.
- `Generate code` button.
- `Use provided code` secure field + submit button.
- One-time result card with copy button.

**Verification:** Manual run can generate code for a staging/local test project without storing the raw code in config.

---

## Task 5: Add post-generation hygiene

**Objective:** Make the one-time-code flow safe by default.

**Behavior:**
- Copy generated code to clipboard.
- Display it once behind a reveal affordance.
- Clear raw code from memory when result card is dismissed.
- Warn if user tries to navigate away before copying.
- Show “Rotate again” if code may have been exposed.

**Verification:** Inspect persisted config and logs after generation; raw code is absent.

---

## Task 6: Wire SWRL/Dan as first config fixture

**Objective:** Make the known SWRL production review flow selectable without hardcoding secrets.

**Safe defaults:**

```text
project_id: swrl-ui
deployment_id: swrl-ui-production-alias
allowed_origin: https://swrl-ui.vercel.app
subject_pattern: https://swrl-ui.vercel.app*
access_code_id pattern: <client-slug>-swrl-ui-review
```

**Verification:** Generate/rotate for a selected rolodex person, then authenticate in SWRL Review mode.

---

## Open questions

1. Should ZenithOS call a public authenticated Hub admin endpoint, or should it call a local Zenith plugin command that performs the approved ECS/RDS mutation path?
   - Recommendation: start with local Zenith plugin bridge for operator-only control; graduate to Hub admin endpoint later.
2. Should generated raw codes ever be persisted in Keychain?
   - Recommendation: no for MVP; clipboard/display once only.
3. Should client/project/deployment metadata live in ZenithOS config, Hub, or both?
   - Recommendation: Hub canonical, ZenithOS caches safe metadata for operator ergonomics.
4. What is the canonical ZenithOS repo path?
   - Resolved during implementation: ZenithOS is nested under the ClaudeHub workspace, while Hub remains the canonical production review-auth backend.

---

## Acceptance criteria

- Operator can select a rolodex person and rotate/generate a review code without terminal commands.
- Raw code is never printed to logs or persisted in config.
- Hub stores only hashed access code and safe metadata.
- SWRL Review mode accepts the generated code.
- If a code is exposed, operator can rotate again from the same UI.
