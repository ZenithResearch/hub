#!/usr/bin/env python3
"""Validate local Frank review-packet acceptance.

Modes:
- submit mode: submit a review through Gateway, poll Cases, load packet.
- case mode: load an existing Cases record and validate its packet.
- packet mode: validate an existing review_packet.json directly.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request


READY_STATUS = "review_packet_ready"
TERMINAL_CASE_STATUSES = {"COMPLETED", "COMPLETE", "FAILED", "BLOCKED"}


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, timeout: float = 10.0) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {body}") from exc


def latest_case_for_sender(cases_url: str, sender: str) -> dict[str, Any] | None:
    for status in ("OPEN", "READY", "IN_PROGRESS", "BLOCKED", "COMPLETED", "FAILED"):
        result = http_json("GET", f"{cases_url}/cases?status={status}&limit=50")
        for case in result.get("cases", []):
            if case.get("sender") == sender:
                return case
    return None


def slot_value(detail: dict[str, Any], name: str) -> Any:
    for slot in detail.get("slots", []):
        if slot.get("name") != name:
            continue
        raw = slot.get("value")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
    return None


def resolve_packet_path(raw_path: str, *, repo_root: Path | None = None) -> Path:
    path = Path(str(raw_path))
    root = repo_root or Path.cwd()
    if not path.exists() and path.is_absolute() and path.parts[:2] == ("/", "hub"):
        host_path = root / Path(*path.parts[2:])
        if host_path.exists():
            path = host_path
    if not path.exists():
        raise AssertionError(f"review_packet_path does not exist locally: {path}")
    return path


def packet_path_from_case_detail(detail: dict[str, Any]) -> str:
    packet_path = slot_value(detail, "review_packet_path")
    if packet_path:
        return str(packet_path)
    # Fallback: Step 5/6/7 result_json may carry the packet path in older runs.
    for step in detail.get("steps", []):
        result_raw = step.get("result_json")
        if not result_raw:
            continue
        try:
            result = json.loads(result_raw)
        except json.JSONDecodeError:
            continue
        packet_path = result.get("review_packet_path")
        if packet_path:
            return str(packet_path)
    raise AssertionError("review_packet_path was not present in case detail")


def load_packet_from_path(raw_path: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    path = resolve_packet_path(raw_path, repo_root=repo_root)
    return json.loads(path.read_text(encoding="utf-8"))


def load_packet(detail: dict[str, Any]) -> dict[str, Any]:
    return load_packet_from_path(packet_path_from_case_detail(detail))


def summarize_packet(packet: dict[str, Any], *, review_id: str | None = None, case_id: str | None = None) -> dict[str, Any]:
    quality = packet.get("quality") or {}
    handoff = packet.get("implementation_handoff") or {}
    tasks = handoff.get("implementation_tasks") or []
    return {
        "review_id": review_id,
        "case_id": case_id,
        "packet_status": quality.get("status"),
        "feedback_item_count": quality.get("feedback_item_count", len(packet.get("feedback_items") or [])),
        "must_fix_before_delegation": quality.get("must_fix_before_delegation") or [],
        "source_binding_statuses": [binding.get("status") for binding in packet.get("source_bindings", []) if isinstance(binding, dict)],
        "implementation_task_count": len(tasks),
        "files_to_inspect_first": handoff.get("files_to_inspect_first", []),
        "recommended_first_files": [task.get("recommended_first_file") for task in tasks if isinstance(task, dict)],
    }


def _binding_by_feedback_id(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    bindings = packet.get("source_bindings") or []
    return {str(binding.get("feedback_item_id")): binding for binding in bindings if isinstance(binding, dict) and binding.get("feedback_item_id")}


def _task_by_feedback_id(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tasks = ((packet.get("implementation_handoff") or {}).get("implementation_tasks")) or []
    return {str(task.get("feedback_item_id")): task for task in tasks if isinstance(task, dict) and task.get("feedback_item_id")}


def validate_packet(packet: dict[str, Any], *, expect_status: str = READY_STATUS, review_id: str | None = None, case_id: str | None = None) -> dict[str, Any]:
    quality = packet.get("quality") or {}
    status = quality.get("status")
    if status != expect_status:
        raise AssertionError(f"packet quality.status is {status!r}, expected {expect_status!r}: {quality}")

    feedback_items = packet.get("feedback_items") or []
    if expect_status != READY_STATUS:
        return summarize_packet(packet, review_id=review_id, case_id=case_id)

    must_fix = quality.get("must_fix_before_delegation") or []
    if must_fix:
        raise AssertionError(f"packet still has must-fix items: {must_fix}")

    binding_by_id = _binding_by_feedback_id(packet)
    task_by_id = _task_by_feedback_id(packet)
    for item in feedback_items:
        feedback_id = str(item.get("id") or "")
        binding = binding_by_id.get(feedback_id)
        if not binding:
            raise AssertionError(f"missing source binding for feedback item {feedback_id}")
        if binding.get("status") != "verified":
            raise AssertionError(f"source binding for {feedback_id} is {binding.get('status')!r}, expected verified")
        for field in ("primary_files", "style_files", "supporting_files"):
            if not isinstance(binding.get(field), list):
                raise AssertionError(f"source binding {feedback_id} missing list field {field}")
        if not binding.get("recommended_first_file"):
            raise AssertionError(f"source binding {feedback_id} missing recommended_first_file for verified source binding")
        task = task_by_id.get(feedback_id)
        if not task:
            raise AssertionError(f"missing implementation task for feedback item {feedback_id}")
        if task.get("source_binding_status") != binding.get("status"):
            raise AssertionError(
                f"implementation task {feedback_id} source_binding_status {task.get('source_binding_status')!r} "
                f"does not match source binding status {binding.get('status')!r}"
            )
        for field in ("primary_files", "style_files", "supporting_files"):
            if not isinstance(task.get(field), list):
                raise AssertionError(f"implementation task {feedback_id} missing list field {field}")
        if binding.get("status") == "verified" and not task.get("recommended_first_file"):
            raise AssertionError(f"implementation task {feedback_id} missing recommended_first_file for verified source binding")

    if feedback_items and not ((packet.get("implementation_handoff") or {}).get("implementation_tasks") or []):
        raise AssertionError("feedback exists but implementation_handoff.implementation_tasks is empty")

    return summarize_packet(packet, review_id=review_id, case_id=case_id)


def assert_packet_ready(packet: dict[str, Any]) -> None:
    validate_packet(packet, expect_status=READY_STATUS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Frank review-packet local acceptance")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080")
    parser.add_argument("--cases-url", default="http://127.0.0.1:8083")
    parser.add_argument("--subject-id", default="http://localhost:3000/?reviewMode=on")
    parser.add_argument("--events-asset-id")
    parser.add_argument("--audio-asset-id")
    parser.add_argument("--sender", default="Franklin-acceptance-script")
    parser.add_argument("--timeout-seconds", "--max-wait-seconds", dest="timeout_seconds", type=float, default=120.0)
    parser.add_argument("--case-id")
    parser.add_argument("--packet-path")
    parser.add_argument("--review-id")
    parser.add_argument("--expect-status", default=READY_STATUS)
    parser.add_argument("--summary-json", action="store_true")
    args = parser.parse_args(argv)

    if args.packet_path:
        return args
    if args.case_id:
        return args
    if bool(args.events_asset_id) != bool(args.audio_asset_id):
        parser.error("submit mode requires both --events-asset-id and --audio-asset-id")
    if not args.events_asset_id or not args.audio_asset_id:
        parser.error("provide --packet-path, --case-id, or both --events-asset-id and --audio-asset-id")
    return args


def submit_review_and_wait(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    review_id = args.review_id or str(uuid.uuid4())
    sender = f"{args.sender}-{review_id[:8]}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    submit_payload = {
        "review_id": review_id,
        "subject_id": args.subject_id,
        "submitted_by": sender,
        "started_at": now,
        "stopped_at": now,
        "duration_ms": 0,
        "asset_ids": [args.events_asset_id, args.audio_asset_id],
        "events_asset_id": args.events_asset_id,
        "audio_asset_id": args.audio_asset_id,
        "metadata": {"acceptance_script": True},
    }
    submit = http_json("POST", f"{args.gateway_url}/v1/reviews", submit_payload)
    if not args.summary_json:
        print(f"SUBMIT {submit}")

    deadline = time.monotonic() + args.timeout_seconds
    case: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        case = latest_case_for_sender(args.cases_url, sender)
        if case:
            if not args.summary_json:
                print(f"POLL {case.get('id')} {case.get('status')}")
            if case.get("status") in TERMINAL_CASE_STATUSES:
                break
        time.sleep(2)
    if not case:
        raise AssertionError(f"no case found for sender {sender}")
    if case.get("status") != "COMPLETED":
        raise AssertionError(f"case ended {case.get('status')}, expected COMPLETED")
    return review_id, case


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    review_id = args.review_id
    case_id = args.case_id

    if args.packet_path:
        packet = load_packet_from_path(args.packet_path)
    else:
        if args.case_id:
            detail = http_json("GET", f"{args.cases_url}/cases/{args.case_id}")
        else:
            review_id, case = submit_review_and_wait(args)
            case_id = str(case["id"])
            detail = http_json("GET", f"{args.cases_url}/cases/{case_id}")
        packet = load_packet(detail)

    summary = validate_packet(packet, expect_status=args.expect_status, review_id=review_id, case_id=case_id)
    if args.summary_json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("ACCEPTANCE PASS")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - script should print concise failure and nonzero.
        print(f"ACCEPTANCE FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
