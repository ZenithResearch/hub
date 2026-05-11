# Frank Review Packet Hardening Follow-up Implementation Plan

> **For Hermes:** Use subagent-driven-development skill if this plan is delegated. Keep this as a post-UI hardening pass; do not reopen the native packet architecture or Kanban runtime migration.

**Goal:** Add the remaining non-blocking Frank review-packet hardening surfaces after the ZenithOS blocked-case Follow Up UI: reusable E2E acceptance script, source-binding ranking/deduplication, and clearer packet readiness/degraded status in case/review UI surfaces.

**Architecture:** Keep `review_packet.json` as the canonical handoff. Add a rerunnable acceptance harness around the already-proven Franklin26 path, refine deterministic source-reference selection inside `services/frank/review_packet.py`, and expose packet quality as presentation data without changing the packet schema unless a test proves a missing field.

**Tech Stack:** Python 3, pytest, Docker Compose, FastAPI Gateway/Cases APIs, ZenithOS SwiftUI case monitor.

---

## Work-Surface Index

Active surface for this plan: **Frank packet post-signoff hardening**.

Named surfaces:
1. Reusable E2E acceptance harness — active first.
2. Source-binding rank/deduplication — active second.
3. Packet readiness/degraded UI visibility — active third.
4. Documentation/changelog and staged-boundary verification — final sync only.

Out of scope:
- No redesign of `review_packet.json` schema v2 unless tests prove a field is missing.
- No Kanban runtime migration or worker dispatch architecture changes.
- No new model/LLM dependency for binding.
- No broad dirty-tree cleanup.
- No live review submission unless the script itself is the explicit acceptance target.

Debt not allowed:
- Inline-only E2E scripts that cannot be rerun.
- Source bindings that mark generic tag matches as verified.
- Duplicate `files_to_inspect_first` entries that obscure the most likely implementation files.
- UI that shows case completion while hiding `review_packet_status` degradation.

---

## Task 1: Add reusable review-packet E2E acceptance script

**Objective:** Turn the successful Franklin26 inline acceptance into a checked-in script that can rerun against local Hub services.

**Files:**
- Create: `scripts/run_review_packet_acceptance.py`
- Test: optional focused pytest for pure helper functions if helpers are extracted.

**Steps:**
1. Create a CLI script that accepts:
   - `--gateway-url` default `http://127.0.0.1:8080`
   - `--cases-url` default `http://127.0.0.1:8083`
   - `--subject-id` default `http://localhost:3000/?reviewMode=on`
   - `--events-asset-id`
   - `--audio-asset-id`
   - `--sender` default `Franklin-acceptance-script`
   - `--timeout-seconds` default `120`
2. Submit a review through `POST /v1/reviews` using supplied assets.
3. Poll `/cases` until the matching sender/review reaches `COMPLETED` or a terminal failure.
4. Load the case detail, locate `review_packet_path`, and read the packet from the local artifact path if available.
5. Assert:
   - case status is `COMPLETED`
   - packet `quality.status == review_packet_ready`
   - `quality.must_fix_before_delegation == []`
   - every feedback item has a `verified` source binding
   - `implementation_handoff.implementation_tasks` is non-empty when feedback exists
6. Print a compact summary and exit non-zero on failure.

**Verification:**
```bash
.venv/bin/python -m py_compile scripts/run_review_packet_acceptance.py
```
Optional live verification when assets are available:
```bash
.venv/bin/python scripts/run_review_packet_acceptance.py \
  --events-asset-id <known-events-asset-id> \
  --audio-asset-id <known-audio-asset-id>
```

---

## Task 2: Improve source-binding rank and deduplication

**Objective:** Make `files_to_inspect_first` prioritize likely source files over broad stylesheet/docs matches and remove duplicate/noisy references.

**Files:**
- Modify: `services/frank/review_packet.py`
- Test: `tests/test_review_packet.py`

**Steps:**
1. Add failing tests with a temporary codebase containing:
   - direct component `.tsx` match
   - stylesheet `.css` matches
   - README/CHANGELOG decoys
   - repeated selector occurrences in the same file
2. Implement deterministic reference scoring:
   - source component files (`.tsx`, `.ts`, `.jsx`, `.js`) before stylesheets
   - files under `src/` before repo-root docs/config
   - exact class/id selector token matches before partial text matches
   - cap repeated references per file/token to avoid CSS spam
3. Deduplicate `files_to_inspect_first` while preserving ranked order.
4. Preserve existing behavior for mapped but unmatched codebases: degraded binding, not fake verification.

**Verification:**
```bash
.venv/bin/python -m pytest tests/test_review_packet.py -q
```

---

## Task 3: Expose packet readiness/degraded status in UI/API presentation

**Objective:** Make case/review surfaces visibly distinguish `case completed` from `packet ready` or `packet degraded`.

**Files:**
- Hub, if API needs a normalized read surface: `services/gateway_http/app.py` or Cases read DTO only if existing detail payload cannot expose packet status.
- ZenithOS UI: `/Users/bananawalnut/claude-hub/repos/workspace/ZenithOS/Sources/ZenithOSUI/Processes/ProcessDetailView.swift`

**Steps:**
1. Inspect current case detail payload for slots/logs containing `review_packet_status`, `review_packet_path`, and `implementation_handoff`.
2. Prefer UI extraction from existing slots/logs; only add a gateway endpoint if existing data is not present.
3. In ZenithOS case detail, render a small packet-status badge/card when packet fields exist:
   - `review_packet_ready` green/ready
   - `needs_source_binding`, `needs_human_review`, `transcript_only`, `failed` as warning/degraded
   - show packet path and must-fix count if present
4. Keep the card presentation-only; do not mutate case state.

**Verification:**
```bash
cd /Users/bananawalnut/claude-hub/repos/workspace/ZenithOS
swift build --target ZenithOSUI
```
If Hub code changes:
```bash
cd /Users/bananawalnut/repos/hub
.venv/bin/python -m py_compile services/gateway_http/app.py
```

---

## Task 4: Commit focused checkpoints

**Objective:** Preserve reviewability in the dirty rescue tree.

**Steps:**
1. Stage only files changed by each task.
2. Run staged artifact scan:
```bash
git diff --cached --name-only | grep -E '(^|/)(\.hermes|\.tmp|logs?|sessions?|auth|.*\.db(-shm|-wal)?$|.*\.lock$|__pycache__|\.pyc$|data/)' || true
```
3. Run `git diff --cached --check`.
4. Commit separately if tasks are independently complete:
   - `test: add review packet acceptance harness`
   - `fix: rank Frank review packet source bindings`
   - `feat: show review packet readiness in case monitor`

---

## Acceptance

This follow-up is complete when:
- The E2E acceptance script exists and compiles.
- Source-binding ranking/dedup tests pass.
- ZenithOS shows packet readiness/degraded status when packet data is present.
- Hub and ZenithOS focused builds/checks pass.
- Commits are narrow and artifact scans are empty.
