# Frank Review Packet Delegation Quality V2 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Keep this as a narrow post-signoff hardening pass over the already-production-ready Frank review-packet path. Do not reopen native pipeline architecture, Kanban runtime migration, Gateway review submission semantics, or ZenithOS blocked-case recovery.

**Goal:** Upgrade the current review-packet hardening from "acceptance passes" to "acceptance output and implementation handoff are stable enough for downstream coding agents to consume without re-ranking, guessing, or rereading the transcript from scratch."

**Architecture:** Preserve `review_packet.json` schema version number 2 and the packet-first/markdown-second boundary. This plan may add backward-compatible derived fields inside existing v2 sections (`source_bindings[*]` and `implementation_handoff.implementation_tasks[*]`) because those sections already exist specifically for implementation handoff enrichment; it must not remove/rename fields or introduce schema version 3 without escalation. Add small pure helper functions for packet validation, source-reference classification, handoff file grouping, and script output modes; cover them with tests before changing runtime code. Keep live E2E optional and explicit; the default verification should be deterministic local tests plus script compilation.

**Tech Stack:** Python 3, pytest/unittest, stdlib `urllib` acceptance script, existing Frank packet builder in `services/frank/review_packet.py`, existing local Gateway/Cases APIs.

---

## Current Baseline

Already complete and committed:
- Native Frank review-packet pipeline.
- Packet schema v2: actionability, negative evidence, source bindings, implementation handoff.
- Verified source binding for mapped local subject URLs.
- Reusable acceptance script: `scripts/run_review_packet_acceptance.py`.
- Host path mapping for `/hub/...` packet artifact paths.
- Basic source-reference ranking/deduplication.
- ZenithOS packet status badge.
- Franklin23 asset acceptance pass through the current script.

Known current limitations:
- Acceptance script always submits a new review; it cannot validate an existing `case_id`, `review_id`, or packet path directly.
- Acceptance script output is human-readable only; it does not have a stable `--json-out` or `--summary-json` mode for agents/CI.
- Packet validation logic is embedded inside the script instead of reusable/testable helpers.
- `source_bindings[*].references` and implementation tasks do not explicitly classify primary source vs style vs supporting files.
- `implementation_handoff.files_to_inspect_first` is ranked but not structured into `primary_files`, `style_files`, and `supporting_files`.
- A packet can be `review_packet_ready` while still forcing downstream agents to infer file roles from extensions.

---

## Work-Surface Index

Active surface: **Frank packet delegation-quality v2**.

Named surfaces:
1. Acceptance validation API — active first.
2. Acceptance script modes/output — active second.
3. Source reference role classification — active third.
4. Implementation handoff file grouping — active fourth.
5. Process docs/skill memory/changelog — final sync only.

Surfaces intentionally not in scope:
- No changes to review submission API shape unless a failing test proves it is necessary.
- No changes to Frank case step ordering or process contract outputs.
- No model/LLM-based source analysis.
- No broad source search beyond deterministic selector tokens.
- No live E2E by default; live E2E may be run once after tests if the user asks or if acceptance script behavior itself changed and known assets are available.
- No ZenithOS UI work in this pass except documentation references; UI badge already exists.
- No process-contract changes. If additive packet handoff fields need docs, update Frank workflow/process documentation as descriptive docs only; do not alter declared process outputs or step ordering.

Debt not allowed:
- Script-only validation logic that cannot be unit tested.
- A `review_packet_ready` packet with no implementation tasks when actionable feedback exists.
- Handoff tasks whose file list is only a flat ranked array.
- File role classification based on hardcoded absolute repo paths instead of relative paths/extensions.
- Dirty-tree broad staging or commits that absorb unrelated rescue work.

---

## Gate Model

Pre-flight gate:
- Inspect status before implementation.
- Allowed Hub files for this plan:
  - `scripts/run_review_packet_acceptance.py`
  - `services/frank/review_packet.py`
  - `tests/test_review_packet.py`
  - a new focused test file if script helpers need one, e.g. `tests/test_review_packet_acceptance_script.py`
  - `docs/plans/2026-05-06-frank-review-packet-delegation-quality-v2-plan.md`
  - `CHANGELOG.md` focused staged blob only if committing
  - optional Frank process/skill docs only if behavior semantics change
- Existing unrelated dirty files remain unstaged.

Revision gates:
- Every production-code behavior change starts with a failing test.
- After each task, run the narrow test that covers it.
- After all tasks, run:
  - `.venv/bin/python -m py_compile scripts/run_review_packet_acceptance.py services/frank/review_packet.py`
  - `.venv/bin/python -m pytest tests/test_review_packet.py -q`
  - any new focused script-helper test file.

Escalation gate:
- If packet schema version 3, a top-level packet section, or a removal/rename of existing v2 fields is required, stop and ask before implementation. Additive derived fields inside existing v2 handoff sections are allowed by this plan when tests cover backward compatibility.
- If live Gateway/Cases data differs from test assumptions, add an acceptance-script flag or validation mode rather than patching runtime services silently.

Abort gate:
- If the change requires modifying Gateway review submission, Cases persistence schema, process contract outputs/step ordering, or Kanban runtime, stop and report scope breach.

---

## Task 0: Establish focused checkpoint boundary

**Objective:** Prevent the dirty rescue tree from absorbing unrelated runtime work.

**Files:**
- Inspect only: repo status and existing staged diff.

**Step 1: Inspect status**

Run:
```bash
cd /Users/bananawalnut/repos/hub
git status --short --branch
git diff --cached --stat
```

Expected:
- Dirty rescue-tree surfaces may exist.
- No staged diff unless intentionally staged by the current task.

**Step 2: Confirm allowed file set**

Before editing, list the files to be touched in the task report. Do not touch broad runtime/kanban/gateway overlay files.

---

## Task 1: Extract packet acceptance validation helpers

**Objective:** Move acceptance script packet checks into reusable pure functions that can be tested without live services.

**Files:**
- Modify: `scripts/run_review_packet_acceptance.py`
- Create or modify test: `tests/test_review_packet_acceptance_script.py`

**Step 1: Write failing tests**

Create tests for pure helper behavior:

```python
def test_validate_packet_ready_accepts_verified_source_bindings():
    packet = {
        "quality": {"status": "review_packet_ready", "must_fix_before_delegation": []},
        "feedback_items": [{"id": "fb_001"}],
        "source_bindings": [{"feedback_item_id": "fb_001", "status": "verified"}],
        "implementation_handoff": {"implementation_tasks": [{"feedback_item_id": "fb_001"}]},
    }
    summary = validate_packet_ready(packet)
    assert summary["packet_status"] == "review_packet_ready"
    assert summary["feedback_item_count"] == 1


def test_validate_packet_ready_rejects_unverified_binding():
    packet = {
        "quality": {"status": "review_packet_ready", "must_fix_before_delegation": []},
        "feedback_items": [{"id": "fb_001"}],
        "source_bindings": [{"feedback_item_id": "fb_001", "status": "deferred"}],
        "implementation_handoff": {"implementation_tasks": [{"feedback_item_id": "fb_001"}]},
    }
    with pytest.raises(AssertionError, match="expected verified"):
        validate_packet_ready(packet)
```

Also test `/hub/...` host path resolution as a pure helper:

```python
def test_resolve_packet_path_maps_container_hub_path_to_repo_root(tmp_path):
    packet = tmp_path / ".hermes/frank_execution/case_x/artifacts/review_packet.json"
    packet.parent.mkdir(parents=True)
    packet.write_text("{}")
    resolved = resolve_packet_path("/hub/.hermes/frank_execution/case_x/artifacts/review_packet.json", repo_root=tmp_path)
    assert resolved == packet
```

**Step 2: Run RED**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet_acceptance_script.py -q
```

Expected: FAIL because helpers do not exist yet or are not importable.

**Step 3: Implement minimal helpers**

In `scripts/run_review_packet_acceptance.py`, add import-safe helpers:
- `resolve_packet_path(raw_path: str, repo_root: Path | None = None) -> Path`
- `validate_packet_ready(packet: dict[str, Any]) -> dict[str, Any]`
- `summarize_packet(packet: dict[str, Any], *, review_id: str | None = None, case_id: str | None = None) -> dict[str, Any]`

Keep `main()` as CLI wrapper only.

**Step 4: Run GREEN**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet_acceptance_script.py -q
.venv/bin/python -m py_compile scripts/run_review_packet_acceptance.py
```

Expected: PASS.

---

## Task 2: Add acceptance script validate-existing modes and JSON output

**Objective:** Let the acceptance harness validate existing artifacts/cases without always submitting a new review, and emit machine-readable summaries.

**Files:**
- Modify: `scripts/run_review_packet_acceptance.py`
- Modify: `tests/test_review_packet_acceptance_script.py`

**Step 1: Write failing tests for argument behavior using helper-level calls**

Avoid live HTTP in unit tests. Extract a parse helper if needed:
- `parse_args(argv: list[str]) -> argparse.Namespace`

Test these expected modes:
- submit mode requires `--events-asset-id` and `--audio-asset-id` when no existing target is provided.
- packet mode accepts `--packet-path /path/to/review_packet.json` without asset IDs.
- case mode accepts `--case-id case_x` without asset IDs.
- `--summary-json` suppresses prose and prints JSON object only.

**Step 2: Run RED**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet_acceptance_script.py -q
```

Expected: FAIL for missing flags/modes.

**Step 3: Implement CLI modes**

Add flags:
- `--case-id`: fetch `/cases/{case_id}`, load packet, validate.
- `--packet-path`: load local packet directly, validate.
- `--review-id`: optional label for summary; do not require unless submit mode.
- `--summary-json`: print only machine-readable JSON summary.
- `--expect-status`: default `review_packet_ready`; pass into validation.

Mode precedence:
1. `--packet-path` validates local packet only; no HTTP.
2. `--case-id` fetches case and validates packet.
3. Otherwise submit mode requires both asset IDs.

**Step 4: Run GREEN**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet_acceptance_script.py -q
.venv/bin/python -m py_compile scripts/run_review_packet_acceptance.py
```

**Step 5: Manual non-live smoke check**

If a recent packet path exists, run:
```bash
.venv/bin/python scripts/run_review_packet_acceptance.py --packet-path <path> --summary-json
```

Expected: JSON summary, exit 0.

---

## Task 3: Classify source references by implementation role

**Objective:** Turn ranked source references into explicit file roles so coding agents do not infer primary/style/supporting files from a flat list.

**Files:**
- Modify: `services/frank/review_packet.py`
- Modify: `tests/test_review_packet.py`

**Step 1: Write failing tests**

Add tests for a helper, e.g. `classify_source_references(refs)` or derived binding fields:

Expected grouping:
```python
refs = [
    {"path": "/repo/src/components/Button.tsx", "relative_path": "src/components/Button.tsx"},
    {"path": "/repo/src/components/Button.css", "relative_path": "src/components/Button.css"},
    {"path": "/repo/README.md", "relative_path": "README.md"},
]
roles = classify_source_references(refs)
assert roles["primary_files"] == ["/repo/src/components/Button.tsx"]
assert roles["style_files"] == ["/repo/src/components/Button.css"]
assert roles["supporting_files"] == ["/repo/README.md"]
```

Also test that each verified source binding includes:
- `primary_files`
- `style_files`
- `supporting_files`
- `files_to_inspect_first` remains backward-compatible flat ranked list.

**Step 2: Run RED**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet.py::ReviewPacketTests::test_source_reference_roles_are_grouped_for_handoff -q
```

Expected: FAIL.

**Step 3: Implement role classification**

Add pure helpers:
- `_source_reference_role(ref) -> Literal["primary", "style", "supporting"]` or simple string.
- `_group_source_reference_files(refs) -> dict[str, list[str]]`.

Rules:
- `.tsx`, `.ts`, `.jsx`, `.js` -> `primary_files`.
- `.css`, `.scss`, `.sass`, `.html` -> `style_files`.
- everything else -> `supporting_files`.
- Deduplicate per group while preserving ranked order.

Add these fields to each source binding when verified. Keep fields present as empty lists when not verified.

**Step 4: Run GREEN**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet.py -q
```

Expected: PASS.

---

## Task 4: Promote file-role grouping into implementation handoff tasks

**Objective:** Make downstream implementation tasks directly carry primary/style/supporting files so agents can choose the first patch surface safely.

**Files:**
- Modify: `services/frank/review_packet.py`
- Modify: `tests/test_review_packet.py`

**Step 1: Write failing tests**

Add a test for `build_review_packet(...)` with a verified binding containing role fields. Assert each implementation task includes:
- `primary_files`
- `style_files`
- `supporting_files`
- `recommended_first_file`

Expected behavior:
- `recommended_first_file` is first primary file if present.
- Else first style file.
- Else first supporting file.
- Else `None`.

**Step 2: Run RED**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet.py::ReviewPacketTests::test_implementation_tasks_include_file_roles_and_recommended_first_file -q
```

Expected: FAIL.

**Step 3: Implement minimal task enrichment**

In `build_implementation_handoff(...)`, copy role fields from the selected binding into each implementation task and compute `recommended_first_file`.

Do not remove or rename existing fields.

**Step 4: Run GREEN**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet.py -q
```

Expected: PASS.

---

## Task 5: Add acceptance guard for handoff file roles

**Objective:** Make acceptance fail when an otherwise-ready packet lacks role-structured implementation file hints.

**Files:**
- Modify: `scripts/run_review_packet_acceptance.py`
- Modify: `tests/test_review_packet_acceptance_script.py`

**Step 1: Write failing tests**

Add tests:
- Ready packet with feedback and verified binding but implementation task lacks `recommended_first_file` -> FAIL.
- Ready packet with `recommended_first_file` and role groups -> PASS.

**Step 2: Run RED**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet_acceptance_script.py -q
```

Expected: FAIL.

**Step 3: Implement validation**

Extend `validate_packet_ready(...)`:
- For each implementation task tied to feedback, require `recommended_first_file` when `source_binding_status == verified`.
- Require role fields exist as lists:
  - `primary_files`
  - `style_files`
  - `supporting_files`

**Step 4: Run GREEN**

Run:
```bash
.venv/bin/python -m pytest tests/test_review_packet_acceptance_script.py -q
.venv/bin/python -m py_compile scripts/run_review_packet_acceptance.py
```

Expected: PASS.

---

## Task 6: Documentation and final verification

**Objective:** Update descriptive docs only for changed handoff semantics and produce a narrow reviewable checkpoint.

**Files:**
- Optional modify: `base/ops/processes/process-queued-review.md` only as descriptive documentation of packet fields; do not change declared outputs, step ordering, or process contract semantics.
- Optional modify: Frank workflow skills only if role grouping changes agent instructions.
- Modify: `CHANGELOG.md` via focused staged blob if committing.

**Step 1: Decide if docs need changing**

If implementation task file-role fields are added, update the process doc and/or Frank skill to say implementation tasks may include backward-compatible derived fields:
- `primary_files`
- `style_files`
- `supporting_files`
- `recommended_first_file`

Do not modify process output variables, step IDs, step ordering, or contract requirements. If helper/script changes only affect developer tooling, keep docs to changelog only.

**Step 2: Run full focused verification**

Run:
```bash
.venv/bin/python -m py_compile scripts/run_review_packet_acceptance.py services/frank/review_packet.py
.venv/bin/python -m pytest tests/test_review_packet.py tests/test_review_packet_acceptance_script.py -q
```

Optional non-live packet validation if a packet path is available:
```bash
.venv/bin/python scripts/run_review_packet_acceptance.py --packet-path <existing-review-packet.json> --summary-json
```

Optional live acceptance only if requested or explicitly needed:
```bash
.venv/bin/python scripts/run_review_packet_acceptance.py \
  --events-asset-id 384c14a9-9dbc-441a-9b4f-695644c0c88a \
  --audio-asset-id 90d25ef3-65a6-4fb3-9cfa-53110022fd2a \
  --sender Franklin23-delegation-quality-v2 \
  --timeout-seconds 180 \
  --summary-json
```

**Step 3: Stage narrow checkpoint**

Run:
```bash
git add scripts/run_review_packet_acceptance.py services/frank/review_packet.py tests/test_review_packet.py tests/test_review_packet_acceptance_script.py docs/plans/2026-05-06-frank-review-packet-delegation-quality-v2-plan.md
# plus optional docs/skills and focused CHANGELOG.md staged blob only if used
git diff --cached --stat
git diff --cached --name-status
git diff --cached --name-only | grep -E '(^|/)(\.hermes|\.tmp|logs?|sessions?|auth|.*\.db(-shm|-wal)?$|.*\.lock$|__pycache__|\.pyc$|data/)' || true
git diff --cached --check
```

Expected:
- Staged files only match this plan.
- Artifact scan empty.

**Step 4: Commit if requested**

Suggested commit:
```bash
git commit -m "feat: harden Frank review packet delegation quality"
```

---

## Acceptance Criteria

This plan is complete when:
- Acceptance script helpers are unit-tested and import-safe.
- Acceptance script can validate by submit, `--case-id`, or `--packet-path`.
- Acceptance script can emit machine-readable `--summary-json`.
- Source bindings include file-role groups.
- Implementation tasks include file-role groups and `recommended_first_file`.
- Acceptance validation fails when a ready packet lacks role-structured handoff file hints.
- Focused tests pass with `.venv/bin/python`.
- Any commit is staged narrowly with artifact scan empty.
