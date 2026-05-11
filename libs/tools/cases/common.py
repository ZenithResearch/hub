from __future__ import annotations

import os
from typing import Any

import httpx


def _cases_url() -> str:
    url = os.environ.get("CASES_HTTP_URL", "").strip()
    if not url:
        raise RuntimeError("CASES_HTTP_URL environment variable is not set")
    return url.rstrip("/")


def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = httpx.request(
        method,
        f"{_cases_url()}{path}",
        json=json_body,
        timeout=20.0,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


def get_case_payload(case_id: str) -> dict[str, Any]:
    return _request("GET", f"/cases/{case_id}")


def find_step(case_payload: dict[str, Any], step_db_row_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    step_row = next((step for step in case_payload.get("steps", []) if step.get("id") == step_db_row_id), None)
    if not step_row:
        raise ValueError(f"step not found in case payload: {step_db_row_id}")
    contract_step = next(
        (step for step in (case_payload.get("contract") or {}).get("steps", []) if step.get("step_id") == step_row.get("step_id")),
        None,
    )
    return step_row, contract_step
