from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ACTIONABILITY_BUCKETS = (
    "actionable_now",
    "needs_human_clarification",
    "design_preference",
    "non_issue",
    "discarded_or_filtered",
)
NON_GOALS = [
    "Do not create ISS notes in the review-processing pipeline.",
    "Do not implement fixes in the review-processing pipeline.",
    "Do not infer a redesign beyond what the reviewer actually said.",
]
DEFAULT_CONSTRAINTS = [
    "Preserve reviewer voice and direct evidence.",
    "Do not invent acceptance criteria beyond the reviewer's stated feedback.",
    "Preserve existing UX unless the reviewer explicitly criticized it.",
]
DEFAULT_DO_NOT_DO = [
    "Do not create ISS notes from this pipeline output.",
    "Do not treat silent gestures as feedback without matching speech.",
    "Do not infer unstated redesign requirements.",
]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _target_from_event(event: dict[str, Any]) -> tuple[str | None, str | None]:
    for field in ("target", "selector", "target_selector", "css_selector"):
        value = event.get(field)
        if value:
            return str(value), f"event.{field}"
    return None, None


def normalize_review_events(events: list[dict[str, Any]] | Any) -> dict[str, Any]:
    """Normalize raw review-capture events into target/stroke/window summaries.

    The current ZenithOS recorder stores useful CSS-ish targets under `target`.
    Older code only looked for selector aliases, which made component resolution empty.
    """
    if not isinstance(events, list):
        events = []

    type_counts: Counter[str] = Counter()
    target_acc: dict[str, dict[str, Any]] = {}
    target_events: list[dict[str, Any]] = []
    stroke_points: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stroke_bounds: dict[str, dict[str, int | None]] = {}
    pointer_events: list[dict[str, Any]] = []

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "unknown")
        type_counts[event_type] += 1
        elapsed_ms = _safe_int(event.get("elapsedMs"), 0)
        event_id = event.get("id")
        target_ref, source = _target_from_event(event)
        x = event.get("x")
        y = event.get("y")

        if target_ref:
            candidate = target_acc.setdefault(
                target_ref,
                {
                    "target_ref": target_ref,
                    "component": target_ref,
                    "source": source,
                    "selectors": [target_ref],
                    "aliases": [],
                    "resolution_status": "candidate",
                    "event_ids": [],
                    "event_count": 0,
                    "first_elapsed_ms": elapsed_ms,
                    "last_elapsed_ms": elapsed_ms,
                    "spatial_hint": None,
                },
            )
            candidate["event_count"] += 1
            candidate["last_elapsed_ms"] = elapsed_ms
            if event_id is not None:
                candidate["event_ids"].append(event_id)
            if x is not None and y is not None and candidate["spatial_hint"] is None:
                candidate["spatial_hint"] = {"x": _safe_int(x), "y": _safe_int(y)}
            target_events.append(
                {
                    "event_id": event_id,
                    "elapsed_ms": elapsed_ms,
                    "target_ref": target_ref,
                    "source": source,
                    "x": _safe_int(x) if x is not None else None,
                    "y": _safe_int(y) if y is not None else None,
                    "type": event_type,
                }
            )

        if event_type in {"pointer-move", "click", "pointer-down", "pointer-up"}:
            pointer_events.append(
                {
                    "event_id": event_id,
                    "elapsed_ms": elapsed_ms,
                    "target_ref": target_ref,
                    "x": _safe_int(x) if x is not None else None,
                    "y": _safe_int(y) if y is not None else None,
                    "type": event_type,
                }
            )

        stroke_id = event.get("strokeId") or event.get("stroke_id")
        if stroke_id:
            stroke_key = str(stroke_id)
            bounds = stroke_bounds.setdefault(
                stroke_key,
                {"min_x": None, "min_y": None, "max_x": None, "max_y": None, "start_ms": None, "end_ms": None},
            )
            if bounds["start_ms"] is None or elapsed_ms < int(bounds["start_ms"]):
                bounds["start_ms"] = elapsed_ms
            if bounds["end_ms"] is None or elapsed_ms > int(bounds["end_ms"]):
                bounds["end_ms"] = elapsed_ms
            if event_type == "stroke-point":
                point = {"event_id": event_id, "elapsed_ms": elapsed_ms, "x": _safe_int(x), "y": _safe_int(y)}
                stroke_points[stroke_key].append(point)
                for key, val, cmp in (("min_x", point["x"], min), ("max_x", point["x"], max), ("min_y", point["y"], min), ("max_y", point["y"], max)):
                    cur = bounds[key]
                    bounds[key] = val if cur is None else cmp(int(cur), int(val))

    stroke_groups = []
    for stroke_id, bounds in sorted(stroke_bounds.items()):
        points = stroke_points.get(stroke_id, [])
        stroke_groups.append(
            {
                "stroke_id": stroke_id,
                "point_count": len(points),
                "start_ms": bounds["start_ms"],
                "end_ms": bounds["end_ms"],
                "bounds": {
                    "min_x": bounds["min_x"],
                    "min_y": bounds["min_y"],
                    "max_x": bounds["max_x"],
                    "max_y": bounds["max_y"],
                },
                "points": points[:50],
            }
        )

    pointer_windows = []
    for idx, event in enumerate(pointer_events):
        if event.get("target_ref"):
            pointer_windows.append(
                {
                    "id": f"ptr_{idx + 1:03d}",
                    "start_ms": event["elapsed_ms"],
                    "end_ms": event["elapsed_ms"],
                    "target_refs": [event["target_ref"]],
                    "event_ids": [event["event_id"]] if event["event_id"] is not None else [],
                }
            )

    return {
        "count": len(events),
        "type_counts": dict(type_counts),
        "target_candidates": sorted(target_acc.values(), key=lambda item: (-int(item["event_count"]), str(item["target_ref"]))),
        "target_events": target_events,
        "stroke_groups": stroke_groups,
        "pointer_windows": pointer_windows,
    }


def _word_text(word: dict[str, Any]) -> str:
    return str(word.get("text") or word.get("word") or "").strip()


def _word_start(word: dict[str, Any]) -> int:
    if word.get("start_ms") is not None:
        return _safe_int(word.get("start_ms"))
    return _safe_int(float(word.get("start") or 0) * 1000)


def _word_end(word: dict[str, Any]) -> int:
    if word.get("end_ms") is not None:
        return _safe_int(word.get("end_ms"))
    return _safe_int(float(word.get("end") or 0) * 1000)


def build_transcript_segments(
    transcript: str,
    words: list[dict[str, Any]] | Any,
    normalized_events: dict[str, Any],
    *,
    window_ms: int = 1500,
) -> list[dict[str, Any]]:
    if not isinstance(words, list) or not words:
        text = str(transcript or "").strip()
        if not text:
            return []
        words = [{"text": piece, "start_ms": 0, "end_ms": 0} for piece in text.split()]

    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        idx = len(segments) + 1
        start_ms = _word_start(current[0])
        end_ms = _word_end(current[-1])
        text = " ".join(_word_text(word) for word in current).strip()
        nearby_events: list[Any] = []
        nearby_targets: list[str] = []
        for target_event in normalized_events.get("target_events") or []:
            elapsed_ms = _safe_int(target_event.get("elapsed_ms"))
            if not (start_ms - window_ms <= elapsed_ms <= end_ms + window_ms):
                continue
            target_ref = str(target_event.get("target_ref") or "")
            if target_ref and target_ref not in nearby_targets:
                nearby_targets.append(target_ref)
            event_id = target_event.get("event_id")
            if event_id is not None and event_id not in nearby_events:
                nearby_events.append(event_id)
        segments.append(
            {
                "id": f"seg_{idx:03d}",
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "word_count": len(current),
                "nearby_target_refs": nearby_targets[:8],
                "nearby_event_ids": nearby_events[:20],
            }
        )
        current.clear()

    for word in words:
        if not isinstance(word, dict):
            continue
        text = _word_text(word)
        if not text:
            continue
        current.append(word)
        if re.search(r"[.!?]$", text) or len(current) >= 28:
            flush()
    flush()
    return segments


_FEEDBACK_RE = re.compile(r"\b(not centered|needs? to|problem|issue|move|should|wrong|does(?:n't| not)|only shows?|show a number)\b", re.I)


def _classify_feedback(text: str) -> str:
    lower = text.lower()
    if "number" in lower or "only show" in lower or "only shows" in lower or "tray is full" in lower:
        return "missing_state"
    if "center" in lower or "move" in lower or "wrong position" in lower or "margin" in lower:
        return "layout"
    if "click" in lower or "doesn't" in lower or "does not" in lower:
        return "interaction"
    return "unknown"


def _normalize_claim(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    lower = cleaned.lower()
    if "x" in lower and "center" in lower:
        return "The dismiss/X control is not centered and should be repositioned inside its visible container."
    if "number" in lower or "tray is full" in lower:
        return "The notification tray/badge state should show the number/count when the tray is full instead of only after exiting."
    if "not centered" in lower:
        return "The referenced UI element is not centered."
    return cleaned


def extract_feedback_items(segments: list[dict[str, Any]], target_candidates: list[dict[str, Any]] | Any) -> list[dict[str, Any]]:
    del target_candidates  # target refs already travel on segments in this deterministic baseline.
    items: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text or not _FEEDBACK_RE.search(text):
            continue
        idx = len(items) + 1
        target_refs = list(dict.fromkeys(str(ref) for ref in (segment.get("nearby_target_refs") or []) if ref))
        items.append(
            {
                "id": f"fb_{idx:03d}",
                "type": _classify_feedback(text),
                "reviewer_quote": text,
                "normalized_claim": _normalize_claim(text),
                "target_refs": target_refs,
                "evidence": {
                    "transcript_segment_ids": [segment.get("id")],
                    "event_ids": list(segment.get("nearby_event_ids") or []),
                    "stroke_ids": [],
                },
                "severity": "medium",
                "confidence": 0.72 if target_refs else 0.55,
            }
        )
    return items


def _binding_matches_feedback(binding: dict[str, Any], feedback: dict[str, Any]) -> bool:
    feedback_id = str(feedback.get("id") or "")
    if feedback_id and str(binding.get("feedback_item_id") or "") == feedback_id:
        return True
    target_refs = {str(ref) for ref in feedback.get("target_refs") or [] if ref}
    binding_refs = {str(ref) for ref in binding.get("target_refs") or [] if ref}
    return bool(target_refs & binding_refs)


def _bindings_for_feedback(feedback: dict[str, Any], source_bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [binding for binding in source_bindings if isinstance(binding, dict) and _binding_matches_feedback(binding, feedback)]


def _binding_status(feedback: dict[str, Any], source_bindings: list[dict[str, Any]]) -> str:
    bindings = _bindings_for_feedback(feedback, source_bindings)
    statuses = {str(binding.get("status") or "").lower() for binding in bindings}
    if statuses & {"verified", "bound"}:
        return "verified"
    if "blocked" in statuses:
        return "blocked"
    if "deferred" in statuses:
        return "deferred"
    return "missing"


def _negative_item(item: dict[str, Any], *, source: str, reason: str) -> dict[str, Any]:
    return {
        "source": source,
        "reason": reason,
        "evidence": item,
    }


def classify_actionability(
    feedback_items: list[dict[str, Any]],
    source_bindings: list[dict[str, Any]],
    *,
    negative_evidence: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in ACTIONABILITY_BUCKETS}
    for item in feedback_items:
        if not isinstance(item, dict):
            continue
        action_item = {
            "feedback_item_id": item.get("id"),
            "type": item.get("type") or "unknown",
            "target_refs": list(item.get("target_refs") or []),
            "confidence": item.get("confidence"),
            "source_binding_status": _binding_status(item, source_bindings),
            "reason": None,
        }
        if item.get("type") in {"approval", "positive", "fragment"}:
            action_item["reason"] = "non-actionable feedback type"
            buckets["non_issue"].append(action_item)
            continue
        if not item.get("target_refs") or float(item.get("confidence") or 0) < 0.6:
            action_item["reason"] = "feedback lacks a resolved target or confidence is low"
            buckets["needs_human_clarification"].append(action_item)
            continue
        status = action_item["source_binding_status"]
        if status == "verified":
            action_item["reason"] = "feedback has target evidence and verified source binding"
            buckets["actionable_now"].append(action_item)
        elif item.get("type") in {"layout", "missing_state", "interaction", "copy"}:
            action_item["reason"] = "feedback is plausible but source binding is not verified"
            buckets["design_preference"].append(action_item)
        else:
            action_item["reason"] = "feedback requires human triage"
            buckets["needs_human_clarification"].append(action_item)

    neg = negative_evidence or {}
    for item in neg.get("silent_annotations") or []:
        if isinstance(item, dict):
            buckets["discarded_or_filtered"].append(_negative_item(item, source="silent_annotations", reason="gesture/stroke without matching speech"))
    for item in neg.get("filtered_points") or []:
        if isinstance(item, dict):
            buckets["discarded_or_filtered"].append(_negative_item(item, source="filtered_points", reason=str(item.get("reason") or "filtered non-actionable point")))
    for item in neg.get("discarded_events") or []:
        if isinstance(item, dict):
            buckets["discarded_or_filtered"].append(_negative_item(item, source="discarded_events", reason=str(item.get("reason") or "discarded event evidence")))
    return buckets


def _component_for_target(target_ref: str, component_names: list[dict[str, Any]]) -> dict[str, Any] | None:
    for component in component_names:
        if not isinstance(component, dict):
            continue
        refs = {str(component.get("component") or ""), str(component.get("target_ref") or "")}
        refs.update(str(selector) for selector in component.get("selectors") or [])
        if target_ref in refs:
            return component
    return None


def _selector_search_tokens(selector: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"\.([A-Za-z0-9_-]+)|#([A-Za-z0-9_-]+)", selector):
        value = next((part for part in token if part), "")
        if value and value not in tokens:
            tokens.append(value)
    return tokens


def _iter_code_files(root: Path):
    ignored_dirs = {".git", "node_modules", "dist", "build", ".next", "coverage", ".turbo", ".cache"}
    suffixes = {".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".sass", ".html", ".md"}
    for path in root.rglob("*"):
        if any(part in ignored_dirs for part in path.parts):
            continue
        if path.is_file() and path.suffix in suffixes:
            yield path


def _line_window(line_number: int) -> str:
    start = max(1, line_number - 2)
    end = line_number + 2
    return f"{start}-{end}"


def _source_reference_score(ref: dict[str, Any]) -> tuple[int, int, int, str]:
    rel = str(ref.get("relative_path") or ref.get("path") or "")
    suffix = Path(rel).suffix
    source_rank = 0 if suffix in {".tsx", ".ts", ".jsx", ".js"} else 1 if suffix in {".css", ".scss", ".sass", ".html"} else 2
    path_rank = 0 if rel.startswith("src/") or "/src/" in rel else 1
    depth_rank = rel.count("/")
    return (source_rank, path_rank, depth_rank, rel)


def _rank_source_references(refs: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for ref in sorted(refs, key=_source_reference_score):
        path = str(ref.get("path") or "")
        if not path or path in seen_files:
            continue
        seen_files.add(path)
        ordered.append(ref)
        if len(ordered) >= limit:
            break
    return ordered


def _source_reference_role(ref: dict[str, Any]) -> str:
    rel = str(ref.get("relative_path") or ref.get("path") or "")
    suffix = Path(rel).suffix.lower()
    if suffix in {".tsx", ".ts", ".jsx", ".js"}:
        return "primary"
    if suffix in {".css", ".scss", ".sass", ".html"}:
        return "style"
    return "supporting"


def _group_source_reference_files(refs: list[dict[str, Any]] | Any) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {"primary_files": [], "style_files": [], "supporting_files": []}
    seen_by_group: dict[str, set[str]] = {key: set() for key in groups}
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict):
            continue
        path = str(ref.get("path") or "")
        if not path:
            continue
        role = _source_reference_role(ref)
        key = f"{role}_files"
        if path in seen_by_group[key]:
            continue
        seen_by_group[key].add(path)
        groups[key].append(path)
    return groups


def _recommended_first_file(primary_files: list[str], style_files: list[str], supporting_files: list[str]) -> str | None:
    for group in (primary_files, style_files, supporting_files):
        if group:
            return group[0]
    return None


def _source_references_for_selectors(selectors: list[str], codebase_root: str | None) -> list[dict[str, Any]]:
    if not codebase_root:
        return []
    root = Path(os.path.expanduser(codebase_root)).resolve()
    if not root.exists() or not root.is_dir():
        return []
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    tokens: list[str] = []
    for selector in selectors:
        for token in _selector_search_tokens(str(selector)):
            if token not in tokens:
                tokens.append(token)
    for path in _iter_code_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for token in tokens:
                if token not in line:
                    continue
                rel = path.relative_to(root).as_posix()
                key = (rel, token)
                if key in seen:
                    continue
                seen.add(key)
                refs.append(
                    {
                        "path": str(path),
                        "relative_path": rel,
                        "lines": _line_window(line_number),
                        "reason": f"selector/token {token!r} matched {rel}:{line_number}",
                        "selector": token,
                    }
                )
    return _rank_source_references(refs)


def build_source_bindings(
    *,
    feedback_items: list[dict[str, Any]],
    component_names: list[dict[str, Any]],
    subject_id: str | None,
    codebase_root: str | None = None,
) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for item in feedback_items:
        if not isinstance(item, dict):
            continue
        target_refs = [str(ref) for ref in item.get("target_refs") or [] if ref]
        first_ref = target_refs[0] if target_refs else ""
        component = _component_for_target(first_ref, component_names) if first_ref else None
        selectors = list(dict.fromkeys([*(component or {}).get("selectors", []), *target_refs])) if component else target_refs
        references = list((component or {}).get("references") or [])
        if not references:
            references = _source_references_for_selectors(selectors, codebase_root)
        file_roles = _group_source_reference_files(references)
        files_to_inspect = [
            *file_roles["primary_files"],
            *file_roles["style_files"],
            *file_roles["supporting_files"],
        ]
        verified = bool(references) and bool(codebase_root)
        status = "verified" if verified else "deferred"
        reason = "source binding verified from local codebase references" if verified else "source binding unavailable until subject_id is mapped to a local codebase root"
        if not target_refs:
            status = "blocked"
            reason = "source binding blocked because feedback has no resolved target refs"
        bindings.append(
            {
                "feedback_item_id": item.get("id"),
                "status": status,
                "reason": reason,
                "component": (component or {}).get("component") or first_ref or None,
                "subject_id": subject_id,
                "target_refs": target_refs,
                "selectors": selectors,
                "references": references,
                "likely_cause": (component or {}).get("likely_cause"),
                "confidence": "high" if verified else ("medium" if target_refs else "low"),
                "caveats": [] if verified else ["No verified source file references were available in the local run."],
                "files_to_inspect_first": files_to_inspect if verified else [],
                "primary_files": file_roles["primary_files"] if verified else [],
                "style_files": file_roles["style_files"] if verified else [],
                "supporting_files": file_roles["supporting_files"] if verified else [],
                "recommended_first_file": _recommended_first_file(file_roles["primary_files"], file_roles["style_files"], file_roles["supporting_files"]) if verified else None,
                "open_questions": [] if verified else [f"Which local codebase maps to subject_id {subject_id or 'unknown'} for target {first_ref or 'unresolved'}?"],
            }
        )
    return bindings


def _acceptance_checks(item: dict[str, Any]) -> list[str]:
    claim = str(item.get("normalized_claim") or item.get("reviewer_quote") or "the stated feedback")
    return [
        f"Verify the reviewed UI no longer exhibits: {claim}",
        "Verify the change is limited to the reviewed surface unless the codebase requires shared component changes.",
        "Verify no unrelated redesign or issue-note creation was introduced.",
    ]


def build_implementation_handoff(packet: dict[str, Any]) -> dict[str, Any]:
    feedback_items = _as_list(packet.get("feedback_items"))
    source_bindings = _as_list(packet.get("source_bindings"))
    actionability = _as_dict(packet.get("actionability"))
    actionable_ids = {
        str(item.get("feedback_item_id"))
        for bucket in ("actionable_now", "design_preference", "needs_human_clarification")
        for item in _as_list(actionability.get(bucket))
        if isinstance(item, dict) and item.get("feedback_item_id")
    }
    tasks: list[dict[str, Any]] = []
    open_questions: list[str] = []
    files_to_inspect: list[str] = []
    verification_notes: list[str] = []
    for item in feedback_items:
        if not isinstance(item, dict):
            continue
        feedback_id = str(item.get("id") or "")
        if feedback_id and feedback_id not in actionable_ids:
            continue
        bindings = _bindings_for_feedback(item, source_bindings)
        binding = bindings[0] if bindings else {}
        for path in binding.get("files_to_inspect_first") or []:
            if path and path not in files_to_inspect:
                files_to_inspect.append(path)
        for question in binding.get("open_questions") or []:
            if question and question not in open_questions:
                open_questions.append(str(question))
        status = str(binding.get("status") or "missing")
        primary_files = list(binding.get("primary_files") or [])
        style_files = list(binding.get("style_files") or [])
        supporting_files = list(binding.get("supporting_files") or [])
        if not (primary_files or style_files or supporting_files):
            role_groups = _group_source_reference_files(list(binding.get("references") or []))
            primary_files = role_groups["primary_files"]
            style_files = role_groups["style_files"]
            supporting_files = role_groups["supporting_files"]
        recommended_first = _recommended_first_file(primary_files, style_files, supporting_files)
        if status in {"deferred", "blocked", "missing"}:
            verification_notes.append(f"{feedback_id or item.get('reviewer_quote')}: source binding {status}; inspect before implementation.")
        evidence = _as_dict(item.get("evidence"))
        target_refs = list(item.get("target_refs") or [])
        tasks.append(
            {
                "feedback_item_id": item.get("id"),
                "title": item.get("normalized_claim") or item.get("reviewer_quote") or "Review feedback item",
                "problem": item.get("normalized_claim") or item.get("reviewer_quote") or "Review feedback item requires triage.",
                "reviewer_quote": item.get("reviewer_quote"),
                "type": item.get("type") or "unknown",
                "severity": item.get("severity") or "medium",
                "confidence": item.get("confidence"),
                "target_refs": target_refs,
                "selectors": list(binding.get("selectors") or target_refs),
                "evidence": {
                    "transcript_segment_ids": list(evidence.get("transcript_segment_ids") or []),
                    "event_ids": list(evidence.get("event_ids") or []),
                    "stroke_ids": list(evidence.get("stroke_ids") or []),
                    "quote": item.get("reviewer_quote"),
                },
                "source_binding_status": status,
                "source_references": list(binding.get("references") or []),
                "files_to_inspect_first": list(binding.get("files_to_inspect_first") or []),
                "primary_files": primary_files,
                "style_files": style_files,
                "supporting_files": supporting_files,
                "recommended_first_file": recommended_first,
                "constraints": list(DEFAULT_CONSTRAINTS),
                "do_not_do": list(DEFAULT_DO_NOT_DO),
                "acceptance_checks": _acceptance_checks(item),
                "open_questions": list(binding.get("open_questions") or []),
            }
        )
    return {
        "implementation_tasks": tasks,
        "open_questions": open_questions,
        "non_goals": list(NON_GOALS),
        "files_to_inspect_first": files_to_inspect,
        "verification_notes": verification_notes,
    }


def packet_quality(packet: dict[str, Any]) -> dict[str, Any]:
    feedback_count = len(packet.get("feedback_items") or [])
    target_count = len(((packet.get("events") or {}).get("target_candidates")) or [])
    transcript_text = str(((packet.get("transcript") or {}).get("text")) or "").strip()
    source_bindings = _as_list(packet.get("source_bindings"))
    actionability = _as_dict(packet.get("actionability"))
    warnings: list[str] = []
    must_fix: list[str] = []
    if not transcript_text:
        status = "failed"
        warnings.append("no transcript available")
    elif feedback_count <= 0:
        status = "transcript_only"
        warnings.append("no feedback items extracted")
    elif actionability and len(actionability.get("needs_human_clarification") or []) >= feedback_count and not (actionability.get("actionable_now") or actionability.get("design_preference")):
        status = "needs_human_review"
        must_fix.append("human clarification required before delegation")
    else:
        missing_or_deferred: list[str] = []
        unresolved_targets: list[str] = []
        for item in packet.get("feedback_items") or []:
            if not isinstance(item, dict):
                continue
            if not item.get("target_refs"):
                unresolved_targets.append(str(item.get("id") or item.get("reviewer_quote") or "unknown feedback"))
            binding_status = _binding_status(item, source_bindings)
            if binding_status not in {"verified"}:
                missing_or_deferred.append(str(item.get("id") or item.get("reviewer_quote") or "unknown feedback"))
        if unresolved_targets:
            status = "needs_human_review"
            must_fix.append("unresolved target refs: " + ", ".join(unresolved_targets))
        elif missing_or_deferred:
            status = "needs_source_binding"
            must_fix.append("source binding missing or deferred for: " + ", ".join(missing_or_deferred))
        else:
            status = "review_packet_ready"
    return {
        "status": status,
        "warnings": warnings,
        "must_fix_before_delegation": must_fix,
        "feedback_item_count": feedback_count,
        "target_candidate_count": target_count,
    }


def build_review_packet(
    slots: dict[str, Any],
    *,
    case_dir: Path,
    target_candidates: list[dict[str, Any]] | None = None,
    normalized_events: dict[str, Any] | None = None,
    segments: list[dict[str, Any]] | None = None,
    feedback_items: list[dict[str, Any]] | None = None,
    source_bindings: list[dict[str, Any]] | None = None,
    silent_annotations: list[dict[str, Any]] | None = None,
    filtered_points: list[dict[str, Any]] | None = None,
    discarded_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    events_summary = normalized_events or {
        "count": 0,
        "type_counts": {},
        "target_candidates": target_candidates or [],
        "target_events": [],
        "stroke_groups": [],
        "pointer_windows": [],
    }
    if target_candidates is not None:
        events_summary = {**events_summary, "target_candidates": target_candidates}
    events_asset_path = slots.get("events_asset_path")
    if not events_asset_path:
        matches = sorted((case_dir / "assets").glob("*/events_*.json"))
        if matches:
            events_asset_path = str(matches[-1])
    source_bindings = source_bindings if source_bindings is not None else _as_list(slots.get("codebase_context"))
    negative_evidence = {
        "silent_annotations": silent_annotations if silent_annotations is not None else _as_list(slots.get("silent_annotations")),
        "filtered_points": filtered_points if filtered_points is not None else _as_list(slots.get("filtered_points")),
        "discarded_events": discarded_events if discarded_events is not None else _as_list(slots.get("discarded_events")),
    }
    packet = {
        "schema_version": 2,
        "review": {
            "review_id": slots.get("review_id"),
            "review_id_short": slots.get("review_id_short") or str(slots.get("review_id") or "")[:8],
            "subject_id": slots.get("subject_id"),
            "submitted_by": slots.get("submitted_by"),
            "reviewed_at": slots.get("reviewed_at"),
            "duration_ms": slots.get("duration_ms"),
        },
        "artifacts": {
            "case_dir": str(case_dir),
            "audio_asset_path": slots.get("audio_asset_path"),
            "events_asset_path": events_asset_path,
            "transcript_note_path": slots.get("transcript_note_path"),
            "review_note_path": slots.get("review_note_path"),
            "review_packet_path": slots.get("review_packet_path"),
        },
        "transcript": {
            "text": slots.get("transcript") or slots.get("resolved_transcript") or "",
            "audio_offset_ms": slots.get("audio_offset_ms") or 0,
            "words": slots.get("words") or [],
        },
        "events": events_summary,
        "segments": segments or [],
        "feedback_items": feedback_items or [],
        "source_bindings": source_bindings,
        "negative_evidence": negative_evidence,
    }
    packet["actionability"] = classify_actionability(packet["feedback_items"], packet["source_bindings"], negative_evidence=negative_evidence)
    packet["implementation_handoff"] = build_implementation_handoff(packet)
    packet["quality"] = packet_quality(packet)
    return _json_safe(packet)


def write_review_packet(path: Path, packet: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
