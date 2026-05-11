#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx


def _cases_url() -> str:
    url = os.environ.get("CASES_HTTP_URL", "").strip()
    if not url:
        raise RuntimeError("CASES_HTTP_URL is required")
    return url.rstrip("/")


def _gateway_url() -> str:
    url = os.environ.get("GATEWAY_HTTP_URL", "").strip()
    if not url:
        raise RuntimeError("GATEWAY_HTTP_URL is required")
    return url.rstrip("/")


def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = httpx.request(method, f"{_cases_url()}{path}", json=json_body, timeout=20.0)
    response.raise_for_status()
    return response.json() if response.content else {}


def _load_case(case_id: str) -> dict[str, Any]:
    return _request("GET", f"/cases/{case_id}")


def _active_runtime_state(step: dict[str, Any]) -> bool:
    runtime = step.get("runtime_state_json") or {}
    return str(runtime.get("status") or "").lower() in {"active", "running"}


def _slots_by_name(case_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {slot["name"]: slot for slot in case_payload.get("slots", [])}


def _contract_step(case_payload: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in (case_payload.get("contract") or {}).get("steps", []):
        if step.get("step_id") == step_id:
            return step
    raise KeyError(step_id)


def _step_inputs_ready(step_contract: dict[str, Any], slots_by_name: dict[str, dict[str, Any]]) -> bool:
    for item in step_contract.get("input_items", []):
        slot = slots_by_name.get(item["name"])
        if not slot or slot.get("value") is None:
            return False
    return True


def _step_outputs_empty(step_contract: dict[str, Any], slots_by_name: dict[str, dict[str, Any]]) -> bool:
    for name in step_contract.get("output_variables", []):
        slot = slots_by_name.get(name)
        if slot and slot.get("value") is not None:
            return False
    return True


def _ready_steps(case_payload: dict[str, Any], executor: str) -> list[dict[str, Any]]:
    slots = _slots_by_name(case_payload)
    ready: list[dict[str, Any]] = []
    for step in case_payload.get("steps", []):
        if str(step.get("status") or "").upper() in {"COMPLETED", "FAILED", "SKIPPED"}:
            continue
        if _active_runtime_state(step):
            continue
        if str(step.get("executor") or "").strip() != executor:
            continue
        step_contract = _contract_step(case_payload, step["step_id"])
        if _step_inputs_ready(step_contract, slots) and _step_outputs_empty(step_contract, slots):
            ready.append(
                {
                    "step_db_row_id": step["id"],
                    "step_id": step["step_id"],
                    "title": step.get("name"),
                    "output_variables": list(step_contract.get("output_variables") or []),
                }
            )
    return ready


def cmd_load_case(args: argparse.Namespace) -> None:
    print(json.dumps(_load_case(args.case_id), indent=2, sort_keys=True))


def cmd_ready_steps(args: argparse.Namespace) -> None:
    payload = _load_case(args.case_id)
    print(json.dumps({"case_id": args.case_id, "ready_steps": _ready_steps(payload, args.executor)}, indent=2, sort_keys=True))


def cmd_start_step(args: argparse.Namespace) -> None:
    runtime_state = {
        "status": "active",
        "agent_run_id": args.agent_run_id,
        "wave_id": args.wave_id,
        "tasks": [],
        "current_focus": None,
        "retry_count": 0,
    }
    result = _request(
        "PUT",
        f"/cases/{args.case_id}/steps/{args.step_db_row_id}/runtime-state",
        json_body={"runtime_state_json": runtime_state},
    )
    print(json.dumps(result, indent=2, sort_keys=True))


def cmd_update_runtime(args: argparse.Namespace) -> None:
    runtime_state = json.loads(args.runtime_json)
    result = _request(
        "PUT",
        f"/cases/{args.case_id}/steps/{args.step_db_row_id}/runtime-state",
        json_body={"runtime_state_json": runtime_state},
    )
    print(json.dumps(result, indent=2, sort_keys=True))


def cmd_complete_step(args: argparse.Namespace) -> None:
    outputs_json = json.loads(args.outputs_json)
    result = _request(
        "POST",
        f"/cases/{args.case_id}/steps/{args.step_db_row_id}/complete-outputs",
        json_body={
            "outputs_json": outputs_json,
            "agent_run_id": args.agent_run_id,
            "notes": json.loads(args.notes_json) if args.notes_json else [],
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))


def cmd_fail_step(args: argparse.Namespace) -> None:
    _request(
        "PUT",
        f"/cases/{args.case_id}/steps/{args.step_db_row_id}",
        json_body={"status": "FAILED"},
    )
    log_result = _request(
        "POST",
        f"/cases/{args.case_id}/logs",
        json_body={
            "step_id": args.step_db_row_id,
            "type": "error",
            "message": args.reason,
            "metadata": {"reason": args.reason, "agent_run_id": args.agent_run_id},
        },
    )
    print(json.dumps({"ok": True, "log_id": log_result.get("log_id")}, indent=2, sort_keys=True))


def cmd_materialize_assets(args: argparse.Namespace) -> None:
    case_payload = _load_case(args.case_id)
    dispatch_packet = (case_payload.get("case") or {}).get("dispatch_packet_json") or {}
    initial_context = dispatch_packet.get("initial_context") or {}
    review_id = str(initial_context.get("review_id") or "").strip()
    events_asset_id = str(initial_context.get("events_asset_id") or "").strip()
    audio_asset_id = str(initial_context.get("audio_asset_id") or "").strip()
    if not review_id or not events_asset_id or not audio_asset_id:
        raise RuntimeError("case is missing review_id, events_asset_id, or audio_asset_id")

    output_dir = args.output_dir or f"/tmp/review_assets/{review_id[:8] or review_id}"
    helper_path = Path(__file__).resolve().parent / "fetch_review_assets.py"
    command = [
        sys.executable,
        str(helper_path),
        "--gateway-url",
        _gateway_url(),
        "--review-id",
        review_id,
        "--events-asset-id",
        events_asset_id,
        "--audio-asset-id",
        audio_asset_id,
        "--output-dir",
        output_dir,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    print(result.stdout.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worker_cli.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    load_case = subparsers.add_parser("load-case")
    load_case.add_argument("--case-id", required=True)
    load_case.set_defaults(func=cmd_load_case)

    materialize = subparsers.add_parser("materialize-assets")
    materialize.add_argument("--case-id", required=True)
    materialize.add_argument("--output-dir")
    materialize.set_defaults(func=cmd_materialize_assets)

    ready_steps = subparsers.add_parser("ready-steps")
    ready_steps.add_argument("--case-id", required=True)
    ready_steps.add_argument("--executor", required=True)
    ready_steps.set_defaults(func=cmd_ready_steps)

    start_step = subparsers.add_parser("start-step")
    start_step.add_argument("--case-id", required=True)
    start_step.add_argument("--step-db-row-id", required=True)
    start_step.add_argument("--agent-run-id", required=True)
    start_step.add_argument("--wave-id", required=True)
    start_step.set_defaults(func=cmd_start_step)

    update_runtime = subparsers.add_parser("update-runtime")
    update_runtime.add_argument("--case-id", required=True)
    update_runtime.add_argument("--step-db-row-id", required=True)
    update_runtime.add_argument("--runtime-json", required=True)
    update_runtime.set_defaults(func=cmd_update_runtime)

    complete_step = subparsers.add_parser("complete-step")
    complete_step.add_argument("--case-id", required=True)
    complete_step.add_argument("--step-db-row-id", required=True)
    complete_step.add_argument("--outputs-json", required=True)
    complete_step.add_argument("--agent-run-id")
    complete_step.add_argument("--notes-json")
    complete_step.set_defaults(func=cmd_complete_step)

    fail_step = subparsers.add_parser("fail-step")
    fail_step.add_argument("--case-id", required=True)
    fail_step.add_argument("--step-db-row-id", required=True)
    fail_step.add_argument("--reason", required=True)
    fail_step.add_argument("--agent-run-id")
    fail_step.set_defaults(func=cmd_fail_step)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
