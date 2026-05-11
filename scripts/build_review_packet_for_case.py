from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from typing import Any

from services.frank.case_pipeline_runner import read_slot_values
from services.frank.review_packet import (
    build_review_packet,
    build_transcript_segments,
    extract_feedback_items,
    normalize_review_events,
    write_review_packet,
)


def fetch_case(cases_url: str, case_id: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{cases_url.rstrip('/')}/cases/{case_id}", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def build_packet_from_case_detail(case_detail: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    slots = read_slot_values(case_detail)
    events = slots.get("events") if isinstance(slots.get("events"), list) else []
    normalized = normalize_review_events(events)
    transcript = str(slots.get("resolved_transcript") or slots.get("transcript") or "")
    words = slots.get("words") if isinstance(slots.get("words"), list) else []
    segments = build_transcript_segments(transcript, words, normalized)
    feedback_items = extract_feedback_items(segments, normalized.get("target_candidates") or [])
    source_bindings = slots.get("codebase_context") if isinstance(slots.get("codebase_context"), list) else []
    return build_review_packet(
        slots,
        case_dir=case_dir,
        normalized_events=normalized,
        segments=segments,
        feedback_items=feedback_items,
        source_bindings=source_bindings,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build review_packet.json for an existing case.")
    parser.add_argument("case_id")
    parser.add_argument("--cases-url", default="http://127.0.0.1:8083")
    parser.add_argument("--execution-root", default="/Users/bananawalnut/repos/hub/.hermes/frank_execution")
    args = parser.parse_args()

    case_detail = fetch_case(args.cases_url, args.case_id)
    case_dir = Path(args.execution_root) / args.case_id
    packet = build_packet_from_case_detail(case_detail, case_dir)
    packet_path = case_dir / "artifacts" / "review_packet.json"
    write_review_packet(packet_path, packet)
    print(f"wrote {packet_path}")
    print(f"feedback_items={len(packet.get('feedback_items') or [])}")
    print(f"target_candidates={len(((packet.get('events') or {}).get('target_candidates')) or [])}")
    print(f"status={((packet.get('quality') or {}).get('status'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
