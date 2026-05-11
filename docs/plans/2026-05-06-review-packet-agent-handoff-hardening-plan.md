# Review Packet Agent Handoff Hardening Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. This is a surgical pre-E2E hardening pass. Do not broaden into Kanban runtime repair, Gateway observability, Matrix, Frank/Sophia role migration, or UI work.

**Goal:** Upgrade Frank's native review-packet pipeline so the final deliverable is a complete downstream-agent handoff: canonical packet, verified/deferred source binding, actionability triage, implementation task payload, non-goals, open questions, human-readable review, and status evidence.

**Architecture:** Keep `review_packet.json` as the single canonical machine handoff. Add deterministic enrichment helpers in `services/frank/review_packet.py`, wire them through `CasePipelineRunner` steps 5-8, and render markdown from the enriched packet only. Treat unavailable codebase binding as explicit `deferred`/`blocked` state, never as successful empty analysis.

**Tech Stack:** Python 3, pytest/unittest, FastAPI/httpx service tests, Docker Compose config validation.

---

## Work-Surface Index

Active surface for this plan: **Review packet final deliverable**.

Named surfaces in play:
1. Packet schema/data model — active.
2. Source binding and actionability classification — active.
3. Agent handoff payload and markdown rendering — active.
4. Review status/artifact registration — active.
5. Process docs/skills alignment — active only as a final sync step.
6. Live E2E execution — explicitly deferred until this plan lands and passes verification.

Surfaces intentionally not in scope:
- Hermes Kanban runtime patch files.
- Cases observability API expansion beyond fields needed for this packet path.
- Gateway API schema changes unless an existing status field cannot carry packet status.
- ZenithOS UI display of the packet.
- Full model-backed component/source analysis. This pass may add deterministic/static binding and explicit deferred placeholders; it must not introduce an LLM dependency before E2E.

Debt not allowed:
- Empty `[]` for missing source binding.
- Markdown-only handoff.
- Feedback items without actionability state.
- Status updated to processed when packet is degraded without recording the degradation.
- Aggregate target spans for repeated targets.
- Untracked files required by the implementation.

---

## Required Final Deliverable Shape

Every successful run must produce a `review_packet.json` with these top-level sections:

```json
{
  "schema_version": 2,
  "review": {},
  "artifacts": {},
  "transcript": {},
  "events": {
    "target_candidates": [],
    "target_events": [],
    "stroke_groups": [],
    "pointer_windows": []
  },
  "segments": [],
  "feedback_items": [],
  "source_bindings": [],
  "actionability": {
    "actionable_now": [],
    "needs_human_clarification": [],
    "design_preference": [],
    "non_issue": [],
    "discarded_or_filtered": []
  },
  "negative_evidence": {
    "silent_annotations": [],
    "filtered_points": [],
    "discarded_events": []
  },
  "implementation_handoff": {
    "implementation_tasks": [],
    "open_questions": [],
    "non_goals": [],
    "files_to_inspect_first": [],
    "verification_notes": []
  },
  "quality": {
    "status": "review_packet_ready|needs_source_binding|needs_human_review|transcript_only|failed",
    "warnings": [],
    "must_fix_before_delegation": []
  }
}
```

A downstream implementation agent should be able to consume `implementation_handoff.implementation_tasks` without rereading the transcript from scratch.

---

## Gate Model

Pre-flight gate:
- `git status --short` reviewed before implementation.
- Only the files listed in this plan may be staged for this checkpoint.
- Existing unrelated dirty files remain unstaged.

Revision gates:
- After each code task, run its targeted tests.
- After Task 5, run a robustness subagent review against the staged diff and packet fixture expectations.
- Any critical/important issue becomes a failing test before code changes.

Escalation gate:
- If source binding requires a missing repo mapping from `subject_id` to a local source path, do not guess. Emit deferred binding in code and ask user after this plan, not during implementation.

Abort gate:
- If changes require modifying unrelated Gateway/cases schema or broad runtime architecture, stop and report scope breach.

---

### Task 0: Establish focused checkpoint boundary

**Objective:** Prevent the rescue tree from absorbing unrelated runtime work into this update.

**Files:**
- Inspect only: repo status.
- Modify later only: files explicitly listed in subsequent tasks.

**Step 1: Inspect current dirty tree**

Run:
```bash
git status --short --untracked-files=all
```

Expected: dirty tree exists; unrelated files remain unstaged.

**Step 2: Confirm no staged changes before implementation**

Run:
```bash
git diff --cached --stat
```

Expected: empty or only this plan file if the user wants the plan staged separately. Do not stage broad changes.

**Step 3: Define allowed implementation file list**

Allowed code/test/docs files for this checkpoint:
```text
services/frank/review_packet.py
services/frank/case_pipeline_runner.py
tests/test_review_packet.py
tests/test_frank_case_pipeline_runner.py
base/ops/processes/process-queued-review.md
rolodex/agents/frank/skills/workflow/review-submission-processing/SKILL.md
rolodex/agents/frank/skills/workflow/dispatch-review-submission/SKILL.md
CHANGELOG.md
```

Do not touch compose, Kanban adapter/materializer/reconciler, Gateway, cases, Matrix, Frank/Sophia SOUL/config, or docker patch files in this plan.

---

### Task 1: Add packet v2 schema helpers and tests

**Objective:** Make the packet schema explicitly represent agent handoff data instead of only feedback extraction.

**Files:**
- Modify: `services/frank/review_packet.py`
- Modify: `tests/test_review_packet.py`

**Step 1: Write failing tests**

Add tests asserting:

```python
def test_build_review_packet_v2_includes_actionability_and_handoff_defaults(self) -> None:
    packet = build_review_packet(
        {
            "review_id": "r1",
            "transcript": "The X should move.",
            "words": [],
        },
        case_dir=Path("/tmp/case"),
        feedback_items=[{
            "id": "fb_001",
            "type": "layout",
            "reviewer_quote": "The X should move.",
            "normalized_claim": "The X should move.",
            "target_refs": ["button.x"],
            "evidence": {"transcript_segment_ids": ["seg_001"], "event_ids": [1]},
            "severity": "medium",
            "confidence": 0.72,
        }],
        source_bindings=[],
    )
    self.assertEqual(packet["schema_version"], 2)
    self.assertIn("actionability", packet)
    self.assertIn("implementation_handoff", packet)
    self.assertIn("must_fix_before_delegation", packet["quality"])
```

```python
def test_packet_quality_flags_missing_source_binding_for_actionable_items(self) -> None:
    packet = build_review_packet(... one actionable feedback item, no source binding ...)
    self.assertEqual(packet["quality"]["status"], "needs_source_binding")
    self.assertIn("source binding missing", " ".join(packet["quality"]["must_fix_before_delegation"]))
```

**Step 2: Run tests to verify failure**

Run:
```bash
python3 -m pytest tests/test_review_packet.py -q
```

Expected: new tests fail because schema_version is 1 and new sections are absent.

**Step 3: Implement minimal schema helpers**

In `services/frank/review_packet.py`, add helpers:

```python
def classify_actionability(feedback_items: list[dict[str, Any]], source_bindings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    ...

def build_implementation_handoff(packet: dict[str, Any]) -> dict[str, Any]:
    ...
```

Rules:
- `actionable_now`: feedback has target refs and at least one binding with `status == "bound"` or `status == "verified"` for that feedback item.
- `needs_human_clarification`: feedback has no target refs, low confidence, or vague normalized claim.
- `design_preference`: layout/copy/aesthetic requests with target refs but no source binding yet.
- `non_issue`: approval/positive/fragment categories if later added.
- `discarded_or_filtered`: preserve filtered points from slots when provided.

`build_review_packet()` must set `schema_version: 2` and populate defaults even when lists are empty.

Explicit negative-evidence wiring:
- Add optional `silent_annotations`, `filtered_points`, and `discarded_events` parameters to `build_review_packet()`.
- Populate packet top-level `negative_evidence` from those parameters and/or slot values.
- `classify_actionability()` must copy every filtered/silent item into `discarded_or_filtered` or `non_issue` with `reason`, `evidence`, and `source` fields.
- Silent strokes/gestures without speech must be represented as negative evidence and must not produce implementation tasks.

**Step 4: Update quality calculation**

Quality status precedence:
1. no transcript: `failed`
2. no feedback: `transcript_only`
3. feedback exists but every item is ambiguous/no target: `needs_human_review`
4. actionable/design feedback exists but source binding is not bound/verified: `needs_source_binding`
5. otherwise: `review_packet_ready`

`must_fix_before_delegation` should include missing source binding and unresolved targets. `warnings` should include degraded-but-not-blocking issues.

**Step 5: Run tests**

Run:
```bash
python3 -m pytest tests/test_review_packet.py -q
```

Expected: all review packet tests pass.

---

### Task 2: Build source binding payloads that are explicit, not empty

**Objective:** Ensure each feedback item has either verified/deferred/blocked source binding state, with reasons and first-inspect hints.

**Files:**
- Modify: `services/frank/review_packet.py`
- Modify: `services/frank/case_pipeline_runner.py`
- Modify: `tests/test_review_packet.py`
- Modify: `tests/test_frank_case_pipeline_runner.py`

**Step 1: Write failing tests**

Add unit tests for a helper like:

```python
def test_build_deferred_source_bindings_preserves_feedback_ids_and_reasons(self) -> None:
    bindings = build_source_bindings(
        feedback_items=[{"id": "fb_001", "target_refs": ["button.x"]}],
        component_names=[{"component": "button.x", "selectors": ["button.x"], "source": "event.target"}],
        subject_id="http://localhost:3000/?reviewMode=on",
        codebase_root=None,
    )
    self.assertEqual(bindings[0]["feedback_item_id"], "fb_001")
    self.assertEqual(bindings[0]["status"], "deferred")
    self.assertIn("source binding unavailable", bindings[0]["reason"])
    self.assertIn("button.x", bindings[0]["selectors"])
```

Integration test:
- Step 6 returns one binding per observation/feedback item, not a single generic placeholder for the whole packet.
- Step 6 includes `files_to_inspect_first` only when verified or inferred from a real mapping; otherwise empty plus `open_questions`.

**Step 2: Run targeted tests to verify failure**

Run:
```bash
python3 -m pytest tests/test_review_packet.py tests/test_frank_case_pipeline_runner.py -q
```

Expected: new tests fail.

**Step 3: Implement source binding helper**

Add in `review_packet.py`:

```python
def build_source_bindings(
    *,
    feedback_items: list[dict[str, Any]],
    component_names: list[dict[str, Any]],
    subject_id: str | None,
    codebase_root: str | None = None,
) -> list[dict[str, Any]]:
    ...
```

Binding shape:

```json
{
  "feedback_item_id": "fb_001",
  "status": "verified|deferred|blocked",
  "reason": "...",
  "component": "...",
  "target_refs": [],
  "selectors": [],
  "references": [],
  "likely_cause": null,
  "confidence": "low|medium|high",
  "caveats": [],
  "files_to_inspect_first": [],
  "open_questions": []
}
```

No empty list may mean success. Empty `references` is allowed only with `status != "verified"` and a non-empty reason.

**Step 4: Wire Step 6**

In `CasePipelineRunner.execute_structured_analysis_baseline(step_6)`:
- Load observations from slots.
- Load component_names from slots.
- Call `build_source_bindings(...)`.
- Return `codebase_context` as the list of bindings.

**Step 5: Run tests**

Run:
```bash
python3 -m pytest tests/test_review_packet.py tests/test_frank_case_pipeline_runner.py -q
```

Expected: pass.

---

### Task 3: Generate implementation handoff tasks from packet evidence

**Objective:** Give a downstream implementation agent an explicit work payload: what to fix, evidence, files to inspect first, non-goals, open questions, and acceptance checks.

**Files:**
- Modify: `services/frank/review_packet.py`
- Modify: `tests/test_review_packet.py`

**Step 1: Write failing tests**

Test that a packet with a layout feedback item and deferred binding produces an implementation task with:
- title
- problem
- evidence quote
- event ids
- target refs/selectors
- files_to_inspect_first
- constraints
- do_not_do
- acceptance_checks
- source_binding_status

Example assertion:

```python
tasks = packet["implementation_handoff"]["implementation_tasks"]
self.assertEqual(tasks[0]["feedback_item_id"], "fb_001")
self.assertIn("The X should move", tasks[0]["problem"])
self.assertIn("Do not create ISS notes", tasks[0]["do_not_do"])
self.assertEqual(tasks[0]["source_binding_status"], "deferred")
```

**Step 2: Run failure**

Run:
```bash
python3 -m pytest tests/test_review_packet.py -q
```

Expected: fails.

**Step 3: Implement handoff builder**

`build_implementation_handoff(packet)` should:
- Create one implementation task per actionable/design/clarification feedback item.
- Preserve quote and normalized claim.
- Include event IDs and segment IDs as evidence.
- Include source binding status and references.
- Include `constraints`: reviewer-voice, no acceptance criteria invented from review alone, preserve existing UX unless explicitly criticized.
- Include `do_not_do`: do not infer unstated redesign, do not create ISS notes in this pipeline, do not treat silent gestures as feedback without speech.
- Include `acceptance_checks`: human-readable checks derived from the normalized claim, not code assertions.
- Include `open_questions` for unresolved targets or deferred binding.
- Include `non_goals`: issue creation, implementation, redesign beyond stated feedback.

**Step 4: Run tests**

Run:
```bash
python3 -m pytest tests/test_review_packet.py -q
```

Expected: pass.

---

### Task 4: Persist enriched packet status and artifacts through steps 5-8

**Objective:** Ensure the live pipeline writes the enriched packet, registers useful artifacts, and records packet status/pointer for downstream consumers.

**Files:**
- Modify: `services/frank/case_pipeline_runner.py`
- Modify: `tests/test_frank_case_pipeline_runner.py`

**Step 1: Write failing integration tests**

Add tests asserting:
- Step 5 output includes `review_packet_path`, `review_packet_status`, `target_events`, and non-empty observations when feedback exists.
- Step 6 output is per-feedback binding list.
- Step 7 rewrites `review_packet.json` after adding `review_note_path` and renders markdown from `implementation_handoff` content.
- Step 8 status payload includes `review_packet_status` and `review_packet_path` where the existing gateway schema allows; if schema only accepts `reason`, put a compact JSON-safe reason/metadata field without breaking compatibility.

**Step 2: Run failing tests**

Run:
```bash
python3 -m pytest tests/test_frank_case_pipeline_runner.py -q
```

Expected: fails until fields are wired.

**Step 3: Update Step 5**

In `execute_structured_analysis_baseline(step_5)`:
- Write packet v2.
- Register/preserve path via output `review_packet_path`.
- Return `review_packet_status` from packet quality.
- Return `target_events` from packet events.

**Step 4: Update Step 7 rendering**

`render_review_document()` must include:
- Packet status.
- Implementation handoff summary.
- For each task: problem, quote, target, source binding status, evidence, open questions.
- Non-goals.
- Clear warning if source binding is deferred.

Do not recreate feedback independent of packet.

**Step 5: Update Step 8**

Status update must preserve:
- `review_note_path`
- `review_packet_status`
- `review_packet_path` if accepted by endpoint, or encode it into `reason`/metadata-compatible field otherwise.

If packet status is `review_packet_ready`, status may be `processed`. If status is degraded, status should not pretend completion without reason. Existing behavior may use `processing` plus reason; keep compatibility but make degradation visible.

**Step 6: Run targeted tests**

Run:
```bash
python3 -m pytest tests/test_frank_case_pipeline_runner.py tests/test_review_packet.py -q
```

Expected: pass.

---

### Task 5: Add negative/edge-case tests for delegation failure modes

**Objective:** Lock the failure modes we already saw or anticipate before E2E.

**Files:**
- Modify: `tests/test_review_packet.py`
- Modify: `tests/test_frank_case_pipeline_runner.py`

**Step 1: Add tests**

Required tests:
1. Repeated target false-positive regression remains and now asserts `implementation_handoff.open_questions` if no nearby target evidence.
2. Silent stroke with no speech is carried into `negative_evidence.silent_annotations` and represented in `actionability.discarded_or_filtered` or `non_issue` with a reason; it must not create an implementation task.
3. Filtered/fragment points are carried into `negative_evidence.filtered_points` and represented in `discarded_or_filtered` with reason/evidence.
4. Feedback with no target refs becomes `needs_human_clarification`, not actionable_now.
5. Feedback with target refs but deferred binding becomes `design_preference` or `needs_source_binding`, not `review_packet_ready`.
6. Unreadable/missing review packet at Step 8 produces visible warning/status reason.
7. Markdown contains a deferred-binding warning when source references are unavailable.

**Step 2: Run tests to verify failure/pass loop**

Run:
```bash
python3 -m pytest tests/test_review_packet.py tests/test_frank_case_pipeline_runner.py -q
```

Expected: pass after Tasks 1-4; if any fail, fix implementation, not tests.

---

### Task 6: Sync process docs and Frank workflow skills

**Objective:** Make the process document and skills match the hardened deliverable, so agents do not regress to markdown-only or empty-source-binding behavior.

**Files:**
- Modify: `base/ops/processes/process-queued-review.md`
- Modify: `rolodex/agents/frank/skills/workflow/review-submission-processing/SKILL.md`
- Modify: `rolodex/agents/frank/skills/workflow/dispatch-review-submission/SKILL.md`

**Step 1: Update process doc**

Ensure it explicitly states:
- `review_packet.json` is canonical.
- Required packet sections include `actionability` and `implementation_handoff`.
- Source binding must be verified/deferred/blocked per feedback item.
- Empty source binding is invalid unless paired with explicit degraded status and reason.
- Step 7 renders from packet.
- Step 8 records packet status/pointer.

**Step 2: Update skills**

Both workflow skills must warn:
- Live path is native Frank case pipeline.
- Do not use old validate/store/summary flow.
- Do not use aggregate target spans.
- Downstream delegation consumes `implementation_handoff.implementation_tasks`.

**Step 3: Verify docs diff**

Run:
```bash
git diff --check -- base/ops/processes/process-queued-review.md rolodex/agents/frank/skills/workflow/review-submission-processing/SKILL.md rolodex/agents/frank/skills/workflow/dispatch-review-submission/SKILL.md
```

Expected: pass.

---

### Task 7: Final verification and robustness review

**Objective:** Prove the surgical update is ready for the next local E2E.

**Files:**
- Inspect: staged diff only.

**Step 1: Static checks**

Run:
```bash
python3 -m py_compile services/frank/case_pipeline_runner.py services/frank/review_packet.py
python3 -m pytest tests/test_review_packet.py tests/test_frank_case_pipeline_runner.py -q
```

Expected:
- py_compile passes for the Python files in this plan's allowed implementation scope.
- targeted pytest passes.

Do **not** run broad `git diff --check` before staging in this dirty repo. After staging only the allowed files, run `git diff --cached --check` as part of the staged checkpoint gate below. If implementation unexpectedly requires additional Python files such as `services/frank/main.py` or `scripts/build_review_packet_for_case.py`, stop and explicitly add them to the allowed file list before compiling/staging them.

**Step 2: Staged artifact scan**

Stage only allowed files, then run:

```bash
git diff --cached --check
git diff --cached --stat
git diff --cached --name-status
git diff --cached --name-only | grep -E '(^|/)(\.hermes|\.tmp|logs?|sessions?|auth|.*\.db(-shm|-wal)?$|.*\.lock$|__pycache__|\.pyc$|data/)' || true
```

Expected:
- staged list contains only allowed files.
- artifact scan is empty.

**Step 3: Robustness subagent review**

Dispatch a reviewer with this exact review brief:

```text
Review this staged diff for robustness, failure modes, and loose ends before live E2E.

Must verify:
1. Packet schema is sufficient for downstream implementation delegation.
2. Every feedback item has explicit actionability and source-binding state.
3. Missing source binding cannot look like success.
4. Markdown is rendered from packet data.
5. Status updates preserve degraded packet status.
6. Repeated-target false-positive regression is protected.
7. Tests cover silent gestures, filtered/fragment points, no-target feedback, deferred binding, degraded packet, and markdown warning.
8. No unrelated dirty-tree files are staged.

Return: APPROVED or REQUEST_CHANGES with blocking issues only.
```

If reviewer returns `REQUEST_CHANGES`, convert each blocking issue into a focused test first, then patch implementation and rerun review.

**Step 4: Final report**

Report:
- Files changed.
- Tests run and results.
- Packet sections now guaranteed.
- Any remaining explicit deferred limitations.
- Whether ready for live E2E.

Do not run live E2E in this plan unless user explicitly authorizes after the plan is implemented.

---

## Commit Boundary

Commit message after implementation and review approval:

```bash
git add services/frank/review_packet.py \
        services/frank/case_pipeline_runner.py \
        tests/test_review_packet.py \
        tests/test_frank_case_pipeline_runner.py \
        base/ops/processes/process-queued-review.md \
        rolodex/agents/frank/skills/workflow/review-submission-processing/SKILL.md \
        rolodex/agents/frank/skills/workflow/dispatch-review-submission/SKILL.md \
        CHANGELOG.md

git commit -m "feat: harden review packet agent handoff"
```

Do not stage unrelated modified files.

---

## Acceptance Criteria

- `review_packet.json` schema_version is 2.
- Packet has `actionability`, `negative_evidence`, and `implementation_handoff` sections.
- Each feedback item is represented in actionability and either source-bound or explicitly deferred/blocked.
- Implementation handoff includes tasks with problem, evidence, target refs, source binding status, constraints, do-not-do, acceptance checks, open questions, and non-goals.
- Packet quality blocks or degrades delegation when source binding is missing.
- Markdown review is rendered from packet and surfaces degraded binding status.
- Review status update preserves packet status and note path.
- Repeated target alignment regression remains protected.
- Silent gestures and filtered/fragment points are preserved as negative evidence and do not become implementation tasks without speech.
- Targeted tests pass.
- Robustness subagent approves.
- Staged checkpoint is narrow and artifact-free.
