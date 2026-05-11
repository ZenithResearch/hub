from __future__ import annotations

import os
from typing import Any

import httpx

from .common import find_step, get_case_payload, _request


def get_case(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    case_id = str(tool_input["case_id"])
    return {"request_id": request_id, "case": get_case_payload(case_id)}


def set_step_running(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    case_id = str(tool_input["case_id"])
    step_db_row_id = str(tool_input["step_db_row_id"])
    result = _request(
        "PUT",
        f"/cases/{case_id}/steps/{step_db_row_id}",
        json_body={"status": "RUNNING"},
    )
    return {"request_id": request_id, "ok": bool(result.get("ok", True)), "step_db_row_id": step_db_row_id}


def write_slot(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    case_id = str(tool_input["case_id"])
    name = str(tool_input["name"])
    value = tool_input["value"]
    agent_run_id = str(tool_input["agent_run_id"])
    result = _request(
        "POST",
        f"/cases/{case_id}/slots",
        json_body={"name": name, "value": value, "agent_run_id": agent_run_id},
    )
    return {
        "request_id": request_id,
        "ok": bool(result.get("ok", True)),
        "slot_name": name,
        "agent_run_id": agent_run_id,
    }


def add_case_log(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    case_id = str(tool_input["case_id"])
    payload = {
        "step_id": tool_input.get("step_db_row_id"),
        "type": str(tool_input["type"]),
        "message": str(tool_input["message"]),
        "metadata": tool_input.get("metadata") or {},
    }
    result = _request("POST", f"/cases/{case_id}/logs", json_body=payload)
    return {"request_id": request_id, "log_id": result.get("log_id", "")}


def update_step_runtime_state(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    case_id = str(tool_input["case_id"])
    step_db_row_id = str(tool_input["step_db_row_id"])
    runtime_state_json = tool_input["runtime_state_json"]
    result = _request(
        "PUT",
        f"/cases/{case_id}/steps/{step_db_row_id}/runtime-state",
        json_body={"runtime_state_json": runtime_state_json},
    )
    return {
        "request_id": request_id,
        "ok": bool(result.get("ok", True)),
        "step_db_row_id": step_db_row_id,
        "runtime_updated_at": result.get("runtime_updated_at"),
    }


def complete_step_outputs(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    case_id = str(tool_input["case_id"])
    step_db_row_id = str(tool_input["step_db_row_id"])
    outputs_json = tool_input["outputs_json"]
    payload = {
        "outputs_json": outputs_json,
        "agent_run_id": tool_input.get("agent_run_id"),
        "notes": tool_input.get("notes") or [],
    }
    result = _request(
        "POST",
        f"/cases/{case_id}/steps/{step_db_row_id}/complete-outputs",
        json_body=payload,
    )
    return {
        "request_id": request_id,
        "ok": bool(result.get("ok", True)),
        "step_db_row_id": step_db_row_id,
        "completed_at": result.get("completed_at"),
    }


def update_review_status(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    review_id = str(tool_input["review_id"])
    status = str(tool_input["status"])
    payload: dict[str, Any] = {"status": status}
    if tool_input.get("review_note_path") is not None:
        payload["review_note_path"] = str(tool_input["review_note_path"])
    if tool_input.get("reason") is not None:
        payload["reason"] = str(tool_input["reason"])
    gateway_url = os.environ.get("GATEWAY_HTTP_URL", "").strip().rstrip("/")
    if not gateway_url:
        raise RuntimeError("GATEWAY_HTTP_URL is required for update_review_status")
    response = httpx.patch(f"{gateway_url}/v1/reviews/{review_id}/status", json=payload, timeout=20.0)
    response.raise_for_status()
    result = response.json()
    return {
        "request_id": request_id,
        "ok": True,
        "review_id": str(result.get("review_id") or review_id),
        "status": str(result.get("status") or status),
    }


def set_step_failed(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    case_id = str(tool_input["case_id"])
    step_db_row_id = str(tool_input["step_db_row_id"])
    reason = str(tool_input["reason"])
    _request(
        "PUT",
        f"/cases/{case_id}/steps/{step_db_row_id}",
        json_body={"status": "FAILED"},
    )
    log_result = _request(
        "POST",
        f"/cases/{case_id}/logs",
        json_body={
            "step_id": step_db_row_id,
            "type": "error",
            "message": reason,
            "metadata": {"reason": reason},
        },
    )
    return {
        "request_id": request_id,
        "ok": True,
        "step_db_row_id": step_db_row_id,
        "log_id": log_result.get("log_id", ""),
    }


def set_step_completed(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    case_id = str(tool_input["case_id"])
    step_db_row_id = str(tool_input["step_db_row_id"])
    case_payload = get_case_payload(case_id)
    _, contract_step = find_step(case_payload, step_db_row_id)
    if contract_step and contract_step.get("output_variables"):
        raise ValueError(
            f"step {step_db_row_id} declares outputs and must complete through slot fulfillment"
        )
    result = _request(
        "PUT",
        f"/cases/{case_id}/steps/{step_db_row_id}",
        json_body={"status": "COMPLETED"},
    )
    return {"request_id": request_id, "ok": bool(result.get("ok", True)), "step_db_row_id": step_db_row_id}
