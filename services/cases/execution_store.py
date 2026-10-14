"""Fenced, single-Case containment of the retired native runner.

These rows are legacy concurrency evidence, not authentication or greenfield
ProcessRuns. All calls execute inside the Cases writer transaction.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from fastapi import HTTPException

from .execution_contract import execution_errors, load_profile, profile_for, runnable, state_errors


def install(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS legacy_execution_claims (
      case_id TEXT PRIMARY KEY REFERENCES cases(id), run_id TEXT NOT NULL UNIQUE REFERENCES case_runs(id),
      fence INTEGER NOT NULL, owner TEXT NOT NULL, request_key TEXT NOT NULL,
      expires_at REAL NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS legacy_claim_requests (
      case_id TEXT NOT NULL REFERENCES cases(id), request_key TEXT NOT NULL,
      request_digest TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES case_runs(id),
      PRIMARY KEY(case_id, request_key));
    CREATE TABLE IF NOT EXISTS legacy_execution_effects (
      id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), run_id TEXT NOT NULL REFERENCES case_runs(id),
      step_run_id TEXT NOT NULL REFERENCES step_runs(id), operation TEXT NOT NULL,
      state TEXT NOT NULL, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(step_run_id, operation));
    """)


def claim_row(conn, case_id):
    return conn.execute(
        "SELECT * FROM legacy_execution_claims WHERE case_id = ?", (case_id,)
    ).fetchone()


def managed(conn, case):
    try:
        contract = json.loads(case["contract_json"] or "{}")
        packet = json.loads(case["dispatch_packet_json"] or "{}")
    except (ValueError, TypeError):
        return True
    if not isinstance(contract, dict) or not isinstance(packet, dict):
        return True
    runtime = packet.get("runtime") or {}
    return bool(
        contract.get("legacy_execution")
        or contract.get("process_hash")
        in load_profile("personal-review-v1")["legacy_source_hashes"]
        or conn.execute(
            "SELECT id FROM case_runs WHERE case_id=? AND runtime_mode='native_case_pipeline' LIMIT 1",
            (case["id"],),
        ).fetchone()
        or not isinstance(runtime, dict)
        or runtime.get("mode") == "native_case_pipeline"
        or claim_row(conn, case["id"])
    )


def require_claim(conn, case_id, run_id, fence, *, allow_released=False):
    row = claim_row(conn, case_id)
    if (
        row is None
        or row["state"] not in ({"active", "released"} if allow_released else {"active"})
        or row["expires_at"] <= time.time()
        or row["run_id"] != run_id
        or str(row["fence"]) != str(fence)
    ):
        raise HTTPException(409, "current unexpired legacy claim and fence are required")
    return row


def claim(conn, case, body, api):
    contract = api["_parse_contract"](case)
    problems = execution_errors(contract) + state_errors(
        contract, [dict(row) for row in api["_step_rows"](conn, case["id"])]
    )
    if problems:
        raise HTTPException(409, {"code": "legacy_execution_ineligible", "reasons": problems})
    if case["status"] in {"COMPLETED", "FAILED"}:
        raise HTTPException(409, "terminal legacy Case cannot be revived")
    key, owner = body.request_key, body.owner
    digest = hashlib.sha256(
        json.dumps(
            {
                "owner": owner,
                "lease_seconds": body.lease_seconds,
                "initial_context": body.initial_context,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    seen = conn.execute(
        "SELECT * FROM legacy_claim_requests WHERE case_id=? AND request_key=?", (case["id"], key)
    ).fetchone()
    current = claim_row(conn, case["id"])
    if seen:
        if seen["request_digest"] != digest:
            raise HTTPException(409, "claim idempotency key reused with a different payload")
        if (
            current is None
            or current["run_id"] != seen["run_id"]
            or current["state"] != "active"
            or current["expires_at"] <= time.time()
        ):
            raise HTTPException(
                409, "claim request is retired; retry requires a new request and attempt"
            )
        return dict(current)
    if current and current["state"] == "active" and current["expires_at"] > time.time():
        raise HTTPException(409, "legacy Case already claimed")
    unknown = conn.execute(
        "SELECT id FROM legacy_execution_effects WHERE case_id=? AND state IN ('unknown','observed')",
        (case["id"],),
    ).fetchone()
    if unknown:
        raise HTTPException(409, {"code": "external_outcome_unknown", "effect_id": unknown["id"]})
    if (
        current is None
        and conn.execute(
            "SELECT id FROM case_runs WHERE case_id=? LIMIT 1", (case["id"],)
        ).fetchone()
    ):
        raise HTTPException(409, "pre-containment attempts require reconciliation before execution")
    if current is None:
        dirty_steps = conn.execute(
            "SELECT id FROM case_steps WHERE case_id=? AND status NOT IN ('READY','PENDING') LIMIT 1",
            (case["id"],),
        ).fetchone()
        produced = set(contract["producer_map"])
        dirty_outputs = any(
            name in produced and row["value"] is not None
            for name, row in api["_slot_rows_by_name"](conn, case["id"]).items()
        )
        if dirty_steps or dirty_outputs:
            raise HTTPException(
                409, "pre-containment progress requires reconciliation before execution"
            )
    t = api["now"]()
    if current:
        conn.execute(
            "UPDATE case_runs SET status='blocked', ended_at=COALESCE(ended_at,?), updated_at=? WHERE id=? AND status IN ('running','queued')",
            (t, t, current["run_id"]),
        )
        conn.execute(
            "UPDATE step_runs SET status='failed', ended_at=COALESCE(ended_at,?), updated_at=? WHERE case_run_id=? AND status='running'",
            (t, t, current["run_id"]),
        )
        conn.execute(
            "UPDATE case_steps SET status='READY', runtime_state_json=NULL, updated_at=? WHERE case_id=? AND status='RUNNING'",
            (t, case["id"]),
        )
    roots = set(contract["root_inputs"])
    for name, value in body.initial_context.items():
        if name not in roots:
            raise HTTPException(422, f"claim context is not a root input: {name}")
        if not api["_matches_variable_type"](contract["variables"][name]["type"], value):
            raise HTTPException(422, f"claim root input type mismatch: {name}")
        try:
            api["_write_slot_value_once"](conn, case["id"], name, value, t)
        except api["SlotWriteConflictError"] as exc:
            raise HTTPException(409, str(exc)) from exc
    run_id = "case_run_" + uuid.uuid4().hex
    fence = current["fence"] + 1 if current else 1
    metadata = {
        "legacy_claim_protocol": 1,
        "attempt_number": fence,
        "prior_attempt_id": current["run_id"] if current else None,
        "process_hash": contract["process_hash"],
    }
    conn.execute(
        "INSERT INTO case_runs (id,case_id,runtime_mode,runner,status,idempotency_key,metadata_json,created_at,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            case["id"],
            "native_case_pipeline",
            owner,
            "running",
            "claim:" + key,
            json.dumps(metadata),
            t,
            t,
            t,
        ),
    )
    conn.execute(
        "INSERT INTO legacy_execution_claims VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET run_id=excluded.run_id,fence=excluded.fence,owner=excluded.owner,request_key=excluded.request_key,expires_at=excluded.expires_at,state=excluded.state,updated_at=excluded.updated_at",
        (case["id"], run_id, fence, owner, key, time.time() + body.lease_seconds, "active", t),
    )
    conn.execute(
        "INSERT INTO legacy_claim_requests VALUES (?,?,?,?)", (case["id"], key, digest, run_id)
    )
    conn.execute(
        "UPDATE cases SET status='IN_PROGRESS', claimed_at=?, completed_at=NULL WHERE id=?",
        (t, case["id"]),
    )
    api["_write_case_log"](
        conn, case["id"], "legacy_attempt", "claimed distinct legacy attempt", metadata=metadata
    )
    return dict(claim_row(conn, case["id"]))


def require_step(conn, case_id, step_id, attempt_id, api, *, allow_completed=False):
    attempt = conn.execute(
        "SELECT * FROM step_runs WHERE id=? AND case_id=? AND step_db_row_id=?",
        (attempt_id, case_id, step_id),
    ).fetchone()
    lease = claim_row(conn, case_id)
    if (
        attempt is None
        or lease is None
        or attempt["case_run_id"] != lease["run_id"]
        or attempt["status"]
        not in ({"running", "completed", "failed"} if allow_completed else {"running"})
    ):
        raise HTTPException(409, "current step attempt required")
    return attempt


def require_ready(conn, case, step_id, api):
    contract = api["_parse_contract"](case)
    values = {
        name: api["_slot_row_to_value"](row)
        for name, row in api["_slot_rows_by_name"](conn, case["id"]).items()
    }
    ready = runnable(
        contract,
        [dict(row) for row in api["_step_rows"](conn, case["id"])],
        values,
        profile=profile_for(contract),
    )
    if step_id not in {step["id"] for step in ready}:
        raise HTTPException(409, "step dependencies or root inputs are not satisfied")


def effect(conn, case_id, body, api):
    step = conn.execute(
        "SELECT * FROM step_runs WHERE id=? AND case_id=?", (body.step_run_id, case_id)
    ).fetchone()
    if step is None:
        raise HTTPException(404, "step attempt not found")
    require_step(conn, case_id, step["step_db_row_id"], body.step_run_id, api)
    case = api["_load_case_row"](conn, case_id)
    handler = profile_for(api["_parse_contract"](case))["handlers"][step["step_id"]]["handler"]
    if body.operation != handler:
        raise HTTPException(409, "effect operation does not match frozen handler")
    effect_id = hashlib.sha256(
        f"legacy-effect.v1:{step['case_run_id']}:{body.step_run_id}:{body.operation}".encode()
    ).hexdigest()
    previous = conn.execute(
        "SELECT * FROM legacy_execution_effects WHERE id=?", (effect_id,)
    ).fetchone()
    t = api["now"]()
    if body.state == "unknown":
        if previous:
            raise HTTPException(409, "effect already dispatched; reconcile instead of replaying")
        conn.execute(
            "INSERT INTO legacy_execution_effects VALUES (?,?,?,?,?,?,?,?,?)",
            (
                effect_id,
                case_id,
                step["case_run_id"],
                body.step_run_id,
                body.operation,
                "unknown",
                "{}",
                t,
                t,
            ),
        )
    else:
        if previous is None or previous["state"] != "unknown" or not body.evidence:
            raise HTTPException(409, "observed effect requires prior dispatch evidence")
        conn.execute(
            "UPDATE legacy_execution_effects SET state='observed',evidence_json=?,updated_at=? WHERE id=?",
            (json.dumps(body.evidence), t, effect_id),
        )
    return {"effect_id": effect_id, "state": body.state}


def require_effect_observation(conn, case, step, attempt_id, api):
    operation = profile_for(api["_parse_contract"](case))["handlers"][step["step_id"]]["handler"]
    if operation not in {"transcribe", "write_status", "daily_note"}:
        return
    evidence = conn.execute(
        "SELECT state FROM legacy_execution_effects WHERE step_run_id=? AND operation=?",
        (attempt_id, operation),
    ).fetchone()
    if evidence is None or evidence["state"] not in {"observed", "committed"}:
        raise HTTPException(409, "external effect observation required before output completion")
