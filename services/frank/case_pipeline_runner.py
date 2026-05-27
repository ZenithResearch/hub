from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from services.frank import stt_client
from services.frank.review_case_automaton import (
    AUTOMATON_TO_GATEWAY_STATUS,
    REVIEW_SCOPE_FULL,
    transition,
)
from services.frank.review_packet import (
    build_review_packet,
    build_source_bindings,
    build_transcript_segments,
    extract_feedback_items,
    normalize_review_events,
    packet_quality,
    write_review_packet,
)

TERMINAL_STEP_STATUSES = {"COMPLETED", "FAILED", "SKIPPED"}
TERMINAL_CASE_STATUSES = {"COMPLETED", "FAILED", "BLOCKED"}
RUNNABLE_STEP_STATUSES = {"READY", "RUNNING", "IN_PROGRESS"}
log = logging.getLogger("frank.case_pipeline")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(payload), encoding="utf-8")


def read_slot_values(case_detail: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for slot in case_detail.get("slots") or []:
        raw = slot.get("value")
        if raw is None:
            values[str(slot["name"])] = None
            continue
        if isinstance(raw, (dict, list, int, float, bool)):
            values[str(slot["name"])] = raw
            continue
        try:
            values[str(slot["name"])] = json.loads(str(raw))
        except json.JSONDecodeError:
            values[str(slot["name"])] = raw
    return values


def step_by_id(case_detail: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    for step in case_detail.get("steps") or []:
        if step.get("step_id") == step_id:
            return step
    return None


def contract_step_by_id(case_detail: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    for step in (case_detail.get("contract") or {}).get("steps") or []:
        if step.get("step_id") == step_id:
            return step
    return None


def resolve_subject_codebase_root(subject_id: str | None) -> str | None:
    subject = str(subject_id or "").strip()
    if not subject:
        return None
    raw_map = os.environ.get("FRANK_SUBJECT_CODEBASE_MAP") or ""
    for entry in raw_map.split(";"):
        if not entry.strip() or "=" not in entry:
            continue
        pattern, root = entry.split("=", 1)
        pattern = pattern.strip()
        root = root.strip()
        if pattern and root and subject.startswith(pattern):
            return root
    if subject.startswith("http://localhost:3000") or subject.startswith("http://host.docker.internal:3000"):
        default_root = os.environ.get("FRANK_DEFAULT_SUBJECT_CODEBASE_ROOT", "/workspace/zenith-hub")
        if Path(default_root).exists():
            return default_root
    return None


def step_is_terminal(step: dict[str, Any] | None) -> bool:
    return str((step or {}).get("status") or "").upper() in TERMINAL_STEP_STATUSES


def case_has_runnable_steps(case_detail: dict[str, Any]) -> bool:
    return any(
        str((step or {}).get("status") or "").upper() in RUNNABLE_STEP_STATUSES
        for step in case_detail.get("steps") or []
        if isinstance(step, dict)
    )


@dataclass(frozen=True)
class PipelineResult:
    case_id: str
    case_run_id: str
    status: str
    completed_step_ids: tuple[str, ...]
    blocked_step_id: str | None = None
    blocked_reason: str | None = None


class CasePipelineRunner:
    """Frank-owned native review pipeline runner.

    The runner is service code. It writes cases outputs before marking step runs
    complete and emits fine-grained observability events for Swift/UI consumers.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        cases_url: str,
        gateway_url: str,
        stt_url: str,
        execution_root: Path,
    ) -> None:
        self.client = client
        self.cases_url = cases_url.rstrip("/")
        self.gateway_url = gateway_url.rstrip("/")
        self.stt_url = stt_url.rstrip("/")
        self.execution_root = execution_root

    async def run(self, case_id: str, dispatch_packet: dict[str, Any]) -> PipelineResult:
        case_dir = self.execution_root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        case_run = await self.create_case_run(case_id, dispatch_packet)
        case_run_id = str(case_run["id"])
        completed: list[str] = []

        await self.update_case_status(case_id, "IN_PROGRESS")
        await self.emit_event(case_run_id, "case_run.started", "native case pipeline started")

        try:
            for step_id in [f"step_{index}" for index in range(1, 10)]:
                case_detail = await self.get_case(case_id)
                case_status = str((case_detail.get("case") or {}).get("status") or "").upper()
                if case_status in TERMINAL_CASE_STATUSES:
                    if case_status == "BLOCKED" and case_has_runnable_steps(case_detail):
                        await self.update_case_status(case_id, "IN_PROGRESS")
                    else:
                        break
                step = step_by_id(case_detail, step_id)
                if not step or step_is_terminal(step):
                    continue
                await self.run_step(case_run_id, case_id, dispatch_packet, case_detail, step, case_dir)
                completed.append(step_id)

            final_case = await self.get_case(case_id)
            final_status = str((final_case.get("case") or {}).get("status") or "OPEN").upper()
            if final_status not in TERMINAL_CASE_STATUSES:
                await self.update_case_status(case_id, "COMPLETED")
                final_status = "COMPLETED"
            await self.update_case_run(case_run_id, "completed")
            await self.emit_event(case_run_id, "case_run.completed", "native case pipeline completed")
            return PipelineResult(case_id=case_id, case_run_id=case_run_id, status=final_status.lower(), completed_step_ids=tuple(completed))
        except Exception as exc:
            await self.update_case_run(case_run_id, "blocked", metadata={"reason": str(exc), "error_type": type(exc).__name__})
            await self.emit_event(case_run_id, "case_run.blocked", str(exc), severity="error")
            await self.update_case_status(case_id, "BLOCKED")
            return PipelineResult(
                case_id=case_id,
                case_run_id=case_run_id,
                status="blocked",
                completed_step_ids=tuple(completed),
                blocked_reason=str(exc),
            )

    async def run_step(
        self,
        case_run_id: str,
        case_id: str,
        dispatch_packet: dict[str, Any],
        case_detail: dict[str, Any],
        step: dict[str, Any],
        case_dir: Path,
    ) -> None:
        step_id = str(step["step_id"])
        contract_step = contract_step_by_id(case_detail, step_id) or {}
        executor_type = "native" if step_id in {"step_1", "step_2", "step_3", "step_8", "step_9"} else "model"
        step_run = await self.create_step_run(case_run_id, case_id, step, executor_type=executor_type)
        step_run_id = str(step_run["id"])
        await self.emit_event(case_run_id, "step.started", f"{step_id} started", step_run_id=step_run_id, metadata={"step_id": step_id})

        try:
            if step_id == "step_1":
                outputs = await self.execute_step_1(case_run_id, step_run_id, dispatch_packet, case_dir)
                await self.complete_output_step(case_id, step, outputs, step_run_id, notes=["Native review setup completed"])
            elif step_id == "step_2":
                outputs = await self.execute_step_2(case_run_id, step_run_id, case_detail, case_dir)
                await self.complete_output_step(case_id, step, outputs, step_run_id, notes=["Native STT transcription completed"])
            elif step_id == "step_3":
                outputs = await self.execute_step_3(case_detail)
                await self.complete_output_step(case_id, step, outputs, step_run_id, notes=["Native component resolution baseline completed"])
            elif step_id in {"step_4", "step_5", "step_6", "step_7"}:
                outputs = await self.execute_structured_analysis_baseline(step_id, case_detail, case_dir)
                await self.complete_output_step(case_id, step, outputs, step_run_id, notes=["Native structured analysis baseline completed"])
            elif step_id == "step_8":
                outputs = await self.execute_step_8(case_detail)
                await self.complete_output_step(case_id, step, outputs, step_run_id, notes=["Review status updated"])
            elif step_id == "step_9":
                await self.execute_step_9(case_run_id, step_run_id, case_detail, case_dir)
                await self.complete_no_output_step(case_id, step, step_run_id, notes=["Daily-note compatibility entry written"])
            else:
                output_names = list(contract_step.get("output_variables") or [])
                if output_names:
                    outputs = {name: None for name in output_names}
                    await self.complete_output_step(case_id, step, outputs, step_run_id, notes=["Native fallback completed"])
                else:
                    await self.complete_no_output_step(case_id, step, step_run_id, notes=["Native fallback completed"])

            await self.update_step_run(step_run_id, "completed")
            await self.emit_event(case_run_id, "step.completed", f"{step_id} completed", step_run_id=step_run_id, metadata={"step_id": step_id})
        except Exception as exc:
            await self.update_step_run(step_run_id, "failed", metadata={"reason": str(exc), "error_type": type(exc).__name__, "step_id": step_id})
            await self.emit_event(case_run_id, "step.failed", str(exc), step_run_id=step_run_id, severity="error", metadata={"step_id": step_id})
            raise

    async def execute_step_1(self, case_run_id: str, step_run_id: str, dispatch_packet: dict[str, Any], case_dir: Path) -> dict[str, Any]:
        context = dispatch_packet.get("initial_context") or {}
        review_id = str(context.get("review_id") or "").strip()
        audio_asset_id = str(context.get("audio_asset_id") or "").strip()
        events_asset_id = str(context.get("events_asset_id") or "").strip()
        if not review_id or not audio_asset_id or not events_asset_id:
            raise RuntimeError("native Step 1 requires review_id, audio_asset_id, and events_asset_id")

        span = await self.create_span(case_run_id, step_run_id, "materialize review assets")
        span_id = str(span["id"])
        review_dir = case_dir / "assets" / review_id[:8]
        review_dir.mkdir(parents=True, exist_ok=True)
        audio_bytes, audio_content_type = await self.fetch_review_asset(audio_asset_id)
        audio_ext = ".webm" if "webm" in audio_content_type else ".bin"
        audio_path = review_dir / f"audio_{audio_asset_id}{audio_ext}"
        audio_path.write_bytes(audio_bytes)
        await self.register_artifact(
            case_run_id,
            step_run_id,
            "audio_asset",
            audio_path,
            content_type=audio_content_type,
        )

        events_bytes, events_content_type = await self.fetch_review_asset(events_asset_id)
        events_path = review_dir / f"events_{events_asset_id}.json"
        events_path.write_bytes(events_bytes)
        await self.register_artifact(
            case_run_id,
            step_run_id,
            "events_asset",
            events_path,
            content_type=events_content_type,
        )
        events_payload = json.loads(events_bytes.decode("utf-8"))
        if not isinstance(events_payload, list):
            raise RuntimeError("events asset must contain a JSON array")
        await self.emit_event(case_run_id, "artifact.written", "review assets materialized", step_run_id=step_run_id, span_id=span_id)
        await self.update_span(span_id, "completed", metadata={"artifact_count": 2})
        return {
            "review_id_short": review_id[:8],
            "audio_asset_path": str(audio_path),
            "events": events_payload,
        }

    async def _post_stt_transcribe(self, audio_path: str, *, attempts: int = 4, retry_delay_s: float = 5.0) -> dict[str, Any]:
        last_exc: Exception | None = None
        audio = Path(audio_path)
        metadata = {
            "tool": "stt-http",
            "audio_basename": audio.name,
            "audio_size_bytes": audio.stat().st_size if audio.exists() and audio.is_file() else None,
        }
        for attempt in range(1, attempts + 1):
            try:
                return await stt_client.transcribe_audio(self.client, audio_path)
            except (httpx.RemoteProtocolError, httpx.TransportError) as exc:
                last_exc = exc
                next_delay_s = retry_delay_s * attempt
                log.warning(
                    "stt_transcribe_transport_error",
                    extra={
                        **metadata,
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "error_type": type(exc).__name__,
                        "next_delay_s": None if attempt >= attempts else next_delay_s,
                    },
                )
                if attempt >= attempts:
                    raise
                await asyncio.sleep(next_delay_s)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("stt transcription did not return a response")

    async def execute_step_2(self, case_run_id: str, step_run_id: str, case_detail: dict[str, Any], case_dir: Path) -> dict[str, Any]:
        slots = read_slot_values(case_detail)
        audio_path = str(slots.get("audio_asset_path") or "").strip()
        if not audio_path:
            raise RuntimeError("native Step 2 requires audio_asset_path")
        span = await self.create_span(case_run_id, step_run_id, "stt-http transcription", metadata={"tool": "stt-http"})
        span_id = str(span["id"])
        await self.emit_event(case_run_id, "tool.call.started", "stt-http transcription started", step_run_id=step_run_id, span_id=span_id, metadata={"tool": "stt-http"})
        payload = await self._post_stt_transcribe(audio_path)
        words = self.normalize_words(payload.get("words") or [])
        audio_offset_ms = int(words[0].get("start_ms") or 0) if words else 0
        transcript_payload = {
            "transcript": str(payload.get("transcript") or ""),
            "audio_offset_ms": audio_offset_ms,
            "words": words,
            "language_code": payload.get("language_code"),
            "model": payload.get("model"),
            "provider": payload.get("provider"),
            "fallback_from_provider": payload.get("fallback_from_provider"),
            "audio_preprocessor": payload.get("audio_preprocessor"),
            "source_audio_artifact": payload.get("source_audio_artifact"),
            "processed_audio_artifact": payload.get("processed_audio_artifact"),
        }
        transcript_path = case_dir / "artifacts" / "transcript.json"
        write_json(transcript_path, transcript_payload)
        await self.register_artifact(case_run_id, step_run_id, "transcript", transcript_path, content_type="application/json")
        await self.emit_event(case_run_id, "tool.call.completed", "stt-http transcription completed", step_run_id=step_run_id, span_id=span_id, metadata={"tool": "stt-http", "model": payload.get("model")})
        await self.update_span(span_id, "completed", metadata={"model": payload.get("model"), "word_count": len(words)})
        return {
            "transcript": transcript_payload["transcript"],
            "audio_offset_ms": audio_offset_ms,
            "words": words,
        }

    async def execute_step_3(self, case_detail: dict[str, Any]) -> dict[str, Any]:
        slots = read_slot_values(case_detail)
        events = slots.get("events") if isinstance(slots.get("events"), list) else []
        normalized = normalize_review_events(events)
        component_names: list[dict[str, Any]] = []
        for candidate in normalized.get("target_candidates") or []:
            target_ref = str(candidate.get("target_ref") or "").strip()
            if not target_ref:
                continue
            component_names.append(
                {
                    "component": target_ref,
                    "source": candidate.get("source") or "event.target",
                    "selectors": list(candidate.get("selectors") or [target_ref]),
                    "aliases": list(candidate.get("aliases") or []),
                    "spatial_hint": candidate.get("spatial_hint"),
                    "resolution_status": candidate.get("resolution_status") or "candidate",
                    "event_ids": list(candidate.get("event_ids") or []),
                    "event_count": candidate.get("event_count") or 0,
                }
            )
        return {"component_names": component_names}

    def build_packet_for_case(self, case_detail: dict[str, Any], case_dir: Path, *, review_note_path: str | None = None) -> dict[str, Any]:
        slots = read_slot_values(case_detail)
        if review_note_path:
            slots = {**slots, "review_note_path": review_note_path}
        events = slots.get("events") if isinstance(slots.get("events"), list) else []
        normalized = normalize_review_events(events)
        words = slots.get("words") if isinstance(slots.get("words"), list) else []
        transcript = str(slots.get("resolved_transcript") or slots.get("transcript") or "")
        segments = build_transcript_segments(transcript, words, normalized)
        feedback_items = extract_feedback_items(segments, normalized.get("target_candidates") or [])
        source_bindings = slots.get("codebase_context") if isinstance(slots.get("codebase_context"), list) else []
        silent_annotations = slots.get("silent_annotations") if isinstance(slots.get("silent_annotations"), list) else normalized.get("stroke_groups") or []
        filtered_points = slots.get("filtered_points") if isinstance(slots.get("filtered_points"), list) else []
        review_packet_path = slots.get("review_packet_path") or str(case_dir / "artifacts" / "review_packet.json")
        return build_review_packet(
            {**slots, "review_packet_path": review_packet_path},
            case_dir=case_dir,
            normalized_events=normalized,
            segments=segments,
            feedback_items=feedback_items,
            source_bindings=source_bindings,
            silent_annotations=silent_annotations,
            filtered_points=filtered_points,
        )

    async def execute_structured_analysis_baseline(self, step_id: str, case_detail: dict[str, Any], case_dir: Path) -> dict[str, Any]:
        slots = read_slot_values(case_detail)
        review_id_short = str(slots.get("review_id_short") or slots.get("review_id") or "review")[:8]
        if step_id == "step_4":
            transcript = str(slots.get("transcript") or "")
            transcript_path = case_dir / "artifacts" / f"transcript_{review_id_short}.md"
            transcript_path.write_text(transcript, encoding="utf-8")
            return {"transcript_note_path": str(transcript_path), "resolved_transcript": transcript}
        if step_id == "step_5":
            packet = self.build_packet_for_case(case_detail, case_dir)
            packet_path = case_dir / "artifacts" / "review_packet.json"
            write_review_packet(packet_path, packet)
            return {
                "observations": packet["feedback_items"],
                "target_events": packet["events"].get("target_events") or [],
                "silent_annotations": packet["negative_evidence"].get("silent_annotations") or [],
                "filtered_points": packet["negative_evidence"].get("filtered_points") or [],
                "review_packet_path": str(packet_path),
                "review_packet_status": packet["quality"].get("status"),
                "actionability": packet.get("actionability") or {},
                "negative_evidence": packet.get("negative_evidence") or {},
                "implementation_handoff": packet.get("implementation_handoff") or {},
            }
        if step_id == "step_6":
            slots = read_slot_values(case_detail)
            packet_path = case_dir / "artifacts" / "review_packet.json"
            observations = slots.get("observations") if isinstance(slots.get("observations"), list) else []
            component_names = slots.get("component_names") if isinstance(slots.get("component_names"), list) else []
            source_bindings = build_source_bindings(
                feedback_items=observations,
                component_names=component_names,
                subject_id=str(slots.get("subject_id") or "") or None,
                codebase_root=resolve_subject_codebase_root(str(slots.get("subject_id") or "") or None),
            )
            if not source_bindings and not observations:
                source_bindings = [
                    {
                        "feedback_item_id": None,
                        "status": "deferred",
                        "reason": "source binding deferred; no feedback items were available to bind",
                        "component": None,
                        "target_refs": [],
                        "selectors": [],
                        "references": [],
                        "likely_cause": None,
                        "confidence": "low",
                        "caveats": ["Review packet contains no feedback items."],
                        "files_to_inspect_first": [],
                        "open_questions": ["Does this review contain actionable spoken feedback?"],
                    }
                ]
            return {
                "codebase_context": source_bindings,
            }
        if step_id == "step_7":
            review_path = case_dir / "artifacts" / f"review_{review_id_short}.md"
            packet = self.build_packet_for_case(case_detail, case_dir, review_note_path=str(review_path))
            packet_path = case_dir / "artifacts" / "review_packet.json"
            write_review_packet(packet_path, packet)
            review_path.write_text(self.render_review_document(slots, packet=packet), encoding="utf-8")
            return {"review_note_path": str(review_path)}
        raise RuntimeError(f"unsupported structured analysis step: {step_id}")

    async def execute_step_8(self, case_detail: dict[str, Any]) -> dict[str, Any]:
        slots = read_slot_values(case_detail)
        review_id = str(slots.get("review_id") or "").strip()
        review_note_path = str(slots.get("review_note_path") or "").strip()
        quality = packet_quality({"transcript": {"text": slots.get("transcript") or slots.get("resolved_transcript") or ""}, "feedback_items": slots.get("observations") or [], "events": {"target_candidates": slots.get("component_names") or []}})
        case_id = str((case_detail.get("case") or {}).get("id") or "").strip()
        packet_path = self.execution_root / case_id / "artifacts" / "review_packet.json" if case_id else None
        if packet_path and packet_path.exists():
            try:
                packet = json.loads(packet_path.read_text(encoding="utf-8"))
                quality = packet.get("quality") or packet_quality(packet)
            except (OSError, json.JSONDecodeError):
                quality.setdefault("warnings", []).append("review_packet unreadable; used slot-derived fallback quality")
        packet_status = str(quality.get("status") or "transcript_only")
        review_packet_path = str(packet_path) if packet_path else ""
        if not review_id:
            raise RuntimeError("native Step 8 requires review_id")
        automaton_event = "review_passed"
        transition_result = transition("review", automaton_event)
        gateway_status = AUTOMATON_TO_GATEWAY_STATUS[transition_result.status]
        response = await self.client.patch(
            f"{self.gateway_url}/v1/reviews/{review_id}/status",
            json={
                "status": gateway_status,
                "automaton_status": transition_result.status,
                "automaton_event": automaton_event,
                "review_note_path": review_note_path or None,
                "review_packet_path": review_packet_path or None,
                "review_packet_status": packet_status,
                "reason": transition_result.status_reason,
                "review_scope": transition_result.review_scope or REVIEW_SCOPE_FULL,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "review_status_updated": {
                "review_id": str(payload.get("review_id") or review_id),
                "status": str(payload.get("status") or gateway_status),
                "automaton_status": transition_result.status,
                "automaton_event": automaton_event,
                "reason": transition_result.status_reason,
                "review_scope": transition_result.review_scope or REVIEW_SCOPE_FULL,
                "review_packet_status": packet_status,
                "review_packet_path": review_packet_path,
                "review_note_path": review_note_path,
            }
        }

    async def execute_step_9(self, case_run_id: str, step_run_id: str, case_detail: dict[str, Any], case_dir: Path) -> None:
        slots = read_slot_values(case_detail)
        review_id_short = str(slots.get("review_id_short") or slots.get("review_id") or "review")[:8]
        note_path = case_dir / "artifacts" / f"daily_note_{datetime.now(timezone.utc).date().isoformat()}.md"
        line = f"- processed review {review_id_short}; review_note_path={slots.get('review_note_path')}\n"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        with note_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        await self.register_artifact(case_run_id, step_run_id, "daily_note", note_path, content_type="text/markdown")
        await self.emit_event(case_run_id, "artifact.written", "daily-note compatibility artifact written", step_run_id=step_run_id)

    def render_review_document(self, slots: dict[str, Any], *, packet: dict[str, Any] | None = None) -> str:
        review_id_short = str(slots.get("review_id_short") or slots.get("review_id") or "review")[:8]
        transcript = str(slots.get("resolved_transcript") or slots.get("transcript") or "")
        packet = packet or {
            "feedback_items": slots.get("observations") or [],
            "quality": packet_quality({"transcript": {"text": transcript}, "feedback_items": slots.get("observations") or [], "events": {"target_candidates": slots.get("component_names") or []}}),
            "implementation_handoff": {"implementation_tasks": [], "open_questions": [], "non_goals": [], "files_to_inspect_first": [], "verification_notes": []},
            "actionability": {},
            "negative_evidence": {},
        }
        feedback_items = list(packet.get("feedback_items") or [])
        quality = packet.get("quality") or packet_quality(packet)
        handoff = packet.get("implementation_handoff") or {}
        implementation_tasks = list(handoff.get("implementation_tasks") or [])
        source_bindings = list(packet.get("source_bindings") or [])
        degraded = quality.get("status") != "review_packet_ready"
        lines = [
            f"# Review {review_id_short}",
            "",
            "## Packet Status",
            "",
            f"Status: `{quality.get('status')}`",
            f"Feedback items: `{quality.get('feedback_item_count', len(feedback_items))}`",
            f"Must fix before delegation: {quality.get('must_fix_before_delegation') or []}",
            "",
        ]
        if degraded:
            lines.extend(
                [
                    "> Warning: this packet is degraded for implementation delegation. Resolve the status items before treating it as ready.",
                    "",
                ]
            )
        lines.extend(["## Implementation Handoff", ""])
        if implementation_tasks:
            for index, task in enumerate(implementation_tasks, start=1):
                evidence = task.get("evidence") or {}
                lines.extend(
                    [
                        f"### Task {index}: {task.get('title') or task.get('problem') or 'Implementation task'}",
                        "",
                        f"Problem: {task.get('problem') or ''}",
                        f"Reviewer quote: \"{task.get('reviewer_quote') or ''}\"",
                        f"Targets: {', '.join(f'`{ref}`' for ref in task.get('target_refs') or []) if task.get('target_refs') else '_unresolved_'}",
                        f"Source binding: `{task.get('source_binding_status') or 'missing'}`",
                        f"Evidence: segments={evidence.get('transcript_segment_ids') or []}, events={evidence.get('event_ids') or []}, strokes={evidence.get('stroke_ids') or []}",
                        f"Files to inspect first: {task.get('files_to_inspect_first') or []}",
                        f"Open questions: {task.get('open_questions') or []}",
                        "Acceptance checks:",
                        *(f"- {check}" for check in (task.get("acceptance_checks") or [])),
                        "Do not do:",
                        *(f"- {rule}" for rule in (task.get("do_not_do") or [])),
                        "",
                    ]
                )
        else:
            lines.extend(["No implementation tasks are currently delegable from this packet.", ""])
        if source_bindings:
            lines.extend(["## Source Binding", ""])
            for binding in source_bindings:
                lines.extend(
                    [
                        f"- `{binding.get('feedback_item_id')}` → `{binding.get('status')}` — {binding.get('reason')}",
                    ]
                )
            lines.append("")
        lines.extend(["## Non-goals", ""])
        for goal in handoff.get("non_goals") or []:
            lines.append(f"- {goal}")
        lines.extend(["", "## Feedback Items", ""])
        if feedback_items:
            for index, item in enumerate(feedback_items, start=1):
                target_refs = item.get("target_refs") or []
                evidence = item.get("evidence") or {}
                lines.extend(
                    [
                        f"### {index}. {item.get('normalized_claim') or item.get('reviewer_quote') or 'Feedback item'}",
                        "",
                        f"Reviewer quote: \"{item.get('reviewer_quote') or ''}\"",
                        f"Type: `{item.get('type') or 'unknown'}` · Confidence: `{item.get('confidence')}` · Severity: `{item.get('severity')}`",
                        f"Targets: {', '.join(f'`{ref}`' for ref in target_refs) if target_refs else '_unresolved_'}",
                        f"Evidence: segments={evidence.get('transcript_segment_ids') or []}, events={evidence.get('event_ids') or []}",
                        "",
                    ]
                )
        else:
            lines.extend(["Transcript was produced, but no feedback items were extracted. This review requires human review.", ""])
        negative_evidence = packet.get("negative_evidence") or {}
        if any(negative_evidence.get(key) for key in ("silent_annotations", "filtered_points", "discarded_events")):
            lines.extend(["## Negative Evidence", ""])
            for key in ("silent_annotations", "filtered_points", "discarded_events"):
                values = negative_evidence.get(key) or []
                if values:
                    lines.append(f"- {key}: {len(values)} item(s) preserved but not turned into implementation tasks")
            lines.append("")
        lines.extend(["## What the reviewer said", "", transcript or "_No transcript content available._", ""])
        return "\n".join(lines)

    async def fetch_review_asset(self, asset_id: str) -> tuple[bytes, str]:
        response = await self.client.get(f"{self.gateway_url}/v1/reviews/assets/{asset_id}", timeout=30.0)
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "application/octet-stream")

    def normalize_words(self, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for word in words:
            text = str(word.get("text") or word.get("word") or "").strip()
            if not text:
                continue
            start = word.get("start_ms")
            end = word.get("end_ms")
            if start is None:
                start = int(float(word.get("start") or 0) * 1000)
            if end is None:
                end = int(float(word.get("end") or 0) * 1000)
            normalized.append({"text": text, "start_ms": int(start), "end_ms": int(end)})
        return normalized

    async def get_case(self, case_id: str) -> dict[str, Any]:
        response = await self.client.get(f"{self.cases_url}/cases/{case_id}", timeout=20.0)
        response.raise_for_status()
        return response.json()

    async def update_case_status(self, case_id: str, status: str) -> None:
        response = await self.client.put(f"{self.cases_url}/cases/{case_id}/status", json={"status": status}, timeout=10.0)
        response.raise_for_status()

    async def create_case_run(self, case_id: str, dispatch_packet: dict[str, Any]) -> dict[str, Any]:
        idempotency_key = f"native_case_pipeline:{case_id}"
        payload = {
            "runtime_mode": "native_case_pipeline",
            "runner": "frank.case_pipeline",
            "status": "running",
            "idempotency_key": idempotency_key,
            "metadata": {"event_type": dispatch_packet.get("event_type")},
        }
        try:
            response = await self.client.post(
                f"{self.cases_url}/cases/{case_id}/runs",
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except httpx.ReadTimeout:
            log.warning(
                "case_run_create_timeout_recovering",
                extra={"case_id": case_id, "idempotency_key": idempotency_key},
            )
            existing = await self.find_case_run_by_idempotency_key(case_id, idempotency_key)
            if existing is not None:
                existing["reused_after_timeout"] = True
                log.info(
                    "case_run_reused_after_timeout",
                    extra={"case_id": case_id, "case_run_id": existing.get("id")},
                )
                return existing
            try:
                retry_response = await self.client.post(
                    f"{self.cases_url}/cases/{case_id}/runs",
                    json=payload,
                    timeout=20.0,
                )
                retry_response.raise_for_status()
                retry_payload = retry_response.json()
                retry_payload["retried_after_timeout"] = True
                return retry_payload
            except Exception:
                log.exception(
                    "case_run_retry_after_timeout_failed",
                    extra={"case_id": case_id, "idempotency_key": idempotency_key},
                )
                raise

    async def find_case_run_by_idempotency_key(self, case_id: str, idempotency_key: str) -> dict[str, Any] | None:
        response = await self.client.get(f"{self.cases_url}/cases/{case_id}/runs", timeout=10.0)
        response.raise_for_status()
        for run in (response.json().get("case_runs") or []):
            if run.get("idempotency_key") == idempotency_key:
                return dict(run)
        return None

    async def update_case_run(self, case_run_id: str, status: str, *, metadata: dict[str, Any] | None = None) -> None:
        response = await self.client.put(
            f"{self.cases_url}/case-runs/{case_run_id}",
            json={"status": status, "metadata": metadata or {}},
            timeout=10.0,
        )
        response.raise_for_status()

    async def create_step_run(self, case_run_id: str, case_id: str, step: dict[str, Any], *, executor_type: str) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.cases_url}/case-runs/{case_run_id}/steps",
            json={
                "case_run_id": case_run_id,
                "step_id": step["step_id"],
                "step_db_row_id": step["id"],
                "idx": step.get("idx"),
                "title": step.get("name"),
                "executor_type": executor_type,
                "status": "running",
                "idempotency_key": f"{case_run_id}:{step['id']}",
                "metadata": {"model_backed": executor_type == "model"},
            },
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    async def update_step_run(self, step_run_id: str, status: str, *, metadata: dict[str, Any] | None = None) -> None:
        response = await self.client.put(
            f"{self.cases_url}/step-runs/{step_run_id}",
            json={"status": status, "metadata": metadata or {}},
            timeout=10.0,
        )
        response.raise_for_status()

    async def create_span(
        self,
        case_run_id: str,
        step_run_id: str,
        name: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.cases_url}/case-runs/{case_run_id}/spans",
            json={
                "case_run_id": case_run_id,
                "step_run_id": step_run_id,
                "name": name,
                "status": "running",
                "metadata": metadata or {},
            },
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()

    async def update_span(self, span_id: str, status: str, *, metadata: dict[str, Any] | None = None) -> None:
        response = await self.client.put(
            f"{self.cases_url}/execution-spans/{span_id}",
            json={"status": status, "metadata": metadata or {}},
            timeout=10.0,
        )
        response.raise_for_status()

    async def emit_event(
        self,
        case_run_id: str,
        event_type: str,
        message: str,
        *,
        severity: str = "info",
        step_run_id: str | None = None,
        span_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        response = await self.client.post(
            f"{self.cases_url}/case-runs/{case_run_id}/events",
            json={
                "case_run_id": case_run_id,
                "step_run_id": step_run_id,
                "span_id": span_id,
                "type": event_type,
                "severity": severity,
                "message": message,
                "metadata": metadata or {},
            },
            timeout=10.0,
        )
        response.raise_for_status()

    async def register_artifact(
        self,
        case_run_id: str,
        step_run_id: str,
        role: str,
        path: Path,
        *,
        content_type: str,
    ) -> None:
        response = await self.client.post(
            f"{self.cases_url}/case-runs/{case_run_id}/artifacts",
            json={
                "case_run_id": case_run_id,
                "step_run_id": step_run_id,
                "role": role,
                "uri": f"dir:{path}",
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "content_type": content_type,
                "redaction_status": "not_applicable",
            },
            timeout=10.0,
        )
        response.raise_for_status()

    async def complete_output_step(self, case_id: str, step: dict[str, Any], outputs: dict[str, Any], step_run_id: str, *, notes: list[str]) -> None:
        response = await self.client.post(
            f"{self.cases_url}/cases/{case_id}/steps/{step['id']}/complete-outputs",
            json={"outputs_json": outputs, "agent_run_id": step_run_id, "notes": notes},
            timeout=30.0,
        )
        response.raise_for_status()

    async def complete_no_output_step(self, case_id: str, step: dict[str, Any], step_run_id: str, *, notes: list[str]) -> None:
        response = await self.client.put(
            f"{self.cases_url}/cases/{case_id}/steps/{step['id']}",
            json={"status": "COMPLETED", "agent_run_id": step_run_id},
            timeout=10.0,
        )
        response.raise_for_status()
        for note in notes:
            await self.append_log(case_id, "info", note, step_id=step["id"], metadata={"step_run_id": step_run_id})

    async def fail_step(self, case_id: str, step: dict[str, Any], step_run_id: str, *, reason: str) -> None:
        response = await self.client.put(
            f"{self.cases_url}/cases/{case_id}/steps/{step['id']}",
            json={"status": "FAILED", "agent_run_id": step_run_id},
            timeout=10.0,
        )
        response.raise_for_status()
        await self.append_log(case_id, "error", reason, step_id=step["id"], metadata={"step_run_id": step_run_id})

    async def append_log(
        self,
        case_id: str,
        log_type: str,
        message: str,
        *,
        step_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        response = await self.client.post(
            f"{self.cases_url}/cases/{case_id}/logs",
            json={"step_id": step_id, "type": log_type, "message": message, "metadata": metadata or {}},
            timeout=10.0,
        )
        response.raise_for_status()
