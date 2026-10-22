"""Closed read-only legacy evidence assessment; no migration/write protocol.

Hashes establish byte/content consistency, not authority or operator approval.
Only a frozen, journal-free SQLite copy may be collected. No legacy GET route,
installer, runtime database, artifact URI or external provider is accessed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

EVIDENCE_SCHEMA = "hub.legacy-migration-evidence.v1"
MAX_RECORDS = 10_000
MAX_EVIDENCE_BYTES = 8_000_000
MAX_DATABASE_BYTES = 512_000_000
TABLE_FIELDS = {
    "cases": "id queue_message_id process_name process_path process_source process_hash contract_json dispatch_packet_json title objective sender status created_at claimed_at completed_at".split(),
    "case_steps": "id case_id idx step_id name executor action args_json status result_json runtime_state_json created_at updated_at runtime_updated_at completed_at".split(),
    "case_slots": "id case_id name value filled_at agent_run_id produced_at".split(),
    "case_runs": "id case_id runtime_mode runner status idempotency_key metadata_json created_at started_at updated_at ended_at".split(),
    "step_runs": "id case_run_id case_id step_id step_db_row_id idx title executor_type status idempotency_key metadata_json created_at started_at updated_at ended_at".split(),
    "legacy_execution_claims": "case_id run_id fence owner request_key expires_at state updated_at".split(),
    "legacy_claim_requests": "case_id request_key request_digest run_id".split(),
    "legacy_execution_effects": "id case_id run_id step_run_id operation state evidence_json created_at updated_at".split(),
    "execution_artifacts": "id case_id case_run_id step_run_id span_id role uri sha256 size_bytes content_type redaction_status metadata_json created_at".split(),
    "execution_events": "id case_id case_run_id step_run_id span_id type severity message metadata_json created_at".split(),
    "execution_spans": "id case_run_id step_run_id parent_span_id name status metadata_json created_at started_at updated_at ended_at".split(),
    "case_logs": "id case_id step_id type message metadata_json created_at".split(),
    "model_task_audits": "id case_id step_id step_db_row_id idempotency_key reference_json audit_record_json created_at updated_at".split(),
}
ROW_KEYS = {"steps": "case_steps", "slots": "case_slots", "runs": "case_runs", "step_runs": "step_runs",
            "claim_requests": "legacy_claim_requests", "effects": "legacy_execution_effects",
            "artifacts": "execution_artifacts", "events": "execution_events", "spans": "execution_spans",
            "logs": "case_logs", "model_task_audits": "model_task_audits"}
ROOT_FIELDS = {"schema", "captured_at", "case", "frozen_contract", "frozen_contract_hash", "root_intent", "claim", "provenance", *ROW_KEYS}


class EvidenceError(ValueError):
    pass


class _CollectedEvidence(dict):
    """In-process source pin, excluded from JSON; not a signature or authority.

    Imported JSON needs a separately selected reviewer pin. Editing a collected
    dictionary cannot update its original full-content digest through its data.
    """
    __slots__ = ("_evidence_sha256",)

    def __init__(self, value):
        super().__init__(value)
        self._evidence_sha256 = digest(value)

    def __setattr__(self, name, value):
        if name != "_evidence_sha256" or hasattr(self, name):
            raise AttributeError("source evidence pin is immutable")
        super().__setattr__(name, value)

    @property
    def evidence_sha256(self):
        return self._evidence_sha256


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _hash(value):
    return type(value) is str and re.fullmatch("[a-f0-9]{64}", value) is not None


def _id(value):
    return type(value) is str and 0 < len(value) <= 256 and value.isascii() and re.fullmatch("[a-zA-Z0-9_.:-]+", value) is not None


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise EvidenceError("duplicate_json_field")
        value[key] = item
    return value


def _decode(raw, *, optional=False):
    if raw is None and optional:
        return None
    if type(raw) is not str or len(raw.encode()) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("malformed_retained_json")
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError("nonfinite_json")))


def _timestamp(value):
    if type(value) is not str:
        raise EvidenceError("malformed_timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise EvidenceError("timestamp_requires_timezone")
    return result.timestamp()


def _matches(spec, value):
    typ = spec.strip().lower()
    if typ.startswith("string"):
        return type(value) is str
    if typ == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    return {"integer": int, "boolean": bool, "array": list, "object": dict}.get(typ) is type(value)


def _snapshot_digest(value):
    return digest({key: item for key, item in value.items() if key != "provenance"})


def _validate_rows(value):
    case = value["case"]
    if type(case) is not dict or set(case) != set(TABLE_FIELDS["cases"]) or not _id(case["id"]):
        raise EvidenceError("malformed_case_evidence")
    for name, table in ROW_KEYS.items():
        rows = value[name]
        if type(rows) is not list or len(rows) > MAX_RECORDS:
            raise EvidenceError("missing_or_unbounded_inventory")
        identities = set()
        for row in rows:
            if type(row) is not dict or set(row) != set(TABLE_FIELDS[table]):
                raise EvidenceError("malformed_inventory_row")
            if "case_id" in row and row["case_id"] != case["id"]:
                raise EvidenceError("foreign_case_inventory")
            identity = row.get("id", row.get("request_key"))
            if identity is None or identity in identities:
                raise EvidenceError("duplicate_inventory_identity")
            if "id" in row and not _id(identity):
                raise EvidenceError("malformed_inventory_identity")
            if "idx" in row and (type(row["idx"]) is not int or row["idx"] < 0):
                raise EvidenceError("malformed_step_ordinal")
            identities.add(identity)
    claim = value["claim"]
    if claim is not None and (type(claim) is not dict or set(claim) != set(TABLE_FIELDS["legacy_execution_claims"]) or claim["case_id"] != case["id"]):
        raise EvidenceError("malformed_claim_evidence")


def _validate_contract(value):
    from services.cases.contract import compile_process_contract
    from services.cases.execution_contract import execution_errors, state_errors
    case, contract = value["case"], value["frozen_contract"]
    source = case["process_source"]
    if type(source) is not str or not source or len(source.encode()) > 1_000_000:
        raise EvidenceError("missing_actual_frozen_source")
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    if not _hash(case["process_hash"]) or case["process_hash"] != source_hash:
        raise EvidenceError("source_hash_mismatch")
    if type(contract) is not dict or not _hash(value["frozen_contract_hash"]) or digest(contract) != value["frozen_contract_hash"]:
        raise EvidenceError("frozen_contract_hash_mismatch")
    if _decode(case["contract_json"]) != contract or contract.get("process_hash") != source_hash:
        raise EvidenceError("stored_contract_source_disagreement")
    compiled = compile_process_contract(source, process_path=case["process_path"])
    if compiled != contract:
        raise EvidenceError("frozen_contract_not_reproduced")
    if execution_errors(contract) or state_errors(contract, value["steps"]):
        raise EvidenceError("unsupported_frozen_execution_contract")
    return source_hash


def _root_intent(value):
    """Intent must bind actual source-owned packet bytes, never invented roots."""
    case, contract, intent = value["case"], value["frozen_contract"], value["root_intent"]
    packet = _decode(case["dispatch_packet_json"], optional=True)
    if (type(intent) is not dict or set(intent) != {"source", "case_id", "queue_message_id", "root_inputs", "packet_sha256"}
            or intent["source"] != "case.dispatch_packet_json.initial_context"
            or intent["case_id"] != case["id"] or intent["queue_message_id"] != case["queue_message_id"]
            or type(packet) is not dict or intent["packet_sha256"] != digest(packet)
            or type(intent["root_inputs"]) is not dict or packet.get("initial_context") != intent["root_inputs"]
            or set(intent["root_inputs"]) != set(contract["root_inputs"])):
        raise EvidenceError("missing_or_unbound_root_intent")
    for name, item in intent["root_inputs"].items():
        if not _matches(contract["variables"][name]["type"], item):
            raise EvidenceError("root_input_type_mismatch")
    return digest(intent)


def _history(value, now):
    case, contract = value["case"], value["frozen_contract"]
    steps = {row["id"]: row for row in value["steps"]}
    by_name = {row["step_id"]: row for row in value["steps"]}
    runs = {row["id"]: row for row in value["runs"]}
    attempts = {row["id"]: row for row in value["step_runs"]}
    reasons, reconciliation, retained = [], [], []
    run_meta, fences = {}, set()
    if case["claimed_at"] is not None and not runs:
        reconciliation.append("claimed_case_without_attempt_inventory")
    if case["completed_at"] is not None and case["status"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
        reasons.append("completion_timestamp_without_terminal_case")
    for row in [case, *[row for name in ROW_KEYS for row in value[name]], *([value["claim"]] if value["claim"] else [])]:
        for key in ("created_at", "updated_at", "started_at", "ended_at", "claimed_at", "completed_at", "runtime_updated_at", "filled_at", "produced_at"):
            if key in row and row[key] is not None:
                if _timestamp(row[key]) > now:
                    raise EvidenceError("future_dated_retained_history")
        if row.get("created_at") and row.get("updated_at") and _timestamp(row["created_at"]) > _timestamp(row["updated_at"]):
            raise EvidenceError("nonmonotonic_retained_timestamps")
        if row.get("started_at") and row.get("ended_at") and _timestamp(row["started_at"]) > _timestamp(row["ended_at"]):
            raise EvidenceError("nonmonotonic_attempt_timestamps")
    for run in runs.values():
        meta = _decode(run["metadata_json"])
        if (run["runtime_mode"] != "native_case_pipeline" or type(meta) is not dict
                or type(meta.get("legacy_claim_protocol")) is not int or meta["legacy_claim_protocol"] != 1 or type(meta.get("attempt_number")) is not int
                or meta["attempt_number"] < 1 or meta["attempt_number"] in fences
                or meta.get("process_hash") != case["process_hash"]):
            raise EvidenceError("unsupported_or_unfenced_attempt")
        if run["status"] not in {"running", "queued", "completed", "blocked", "failed", "cancelled"}:
            raise EvidenceError("unsupported_attempt_status")
        fences.add(meta["attempt_number"]); run_meta[run["id"]] = meta
        if run["status"] in {"running", "queued"}:
            reconciliation.append("unfinished_legacy_attempt")
        prior = meta.get("prior_attempt_id")
        if prior is not None and (prior not in runs or prior == run["id"]):
            raise EvidenceError("missing_prior_attempt")
    if fences != set(range(1, len(runs) + 1)):
        raise EvidenceError("incomplete_fence_history")
    fence_runs = {meta["attempt_number"]: run_id for run_id, meta in run_meta.items()}
    for run_id, meta in run_meta.items():
        if meta.get("prior_attempt_id") != fence_runs.get(meta["attempt_number"] - 1):
            raise EvidenceError("incomplete_prior_attempt_chain")
    claims = {row["run_id"]: row for row in value["claim_requests"]}
    if len(claims) != len(value["claim_requests"]):
        raise EvidenceError("duplicate_attempt_claim_binding")
    for row in value["claim_requests"]:
        if (row["run_id"] not in runs or not _hash(row["request_digest"])
                or type(row["request_key"]) is not str or not 0 < len(row["request_key"]) <= 256
                or runs[row["run_id"]]["idempotency_key"] != "claim:" + row["request_key"]):
            raise EvidenceError("missing_or_malformed_claim_binding")
    if set(claims) != set(runs):
        raise EvidenceError("incomplete_claim_inventory")
    claim = value["claim"]
    if runs and claim is None:
        reconciliation.append("missing_current_claim_and_fence")
    if claim is not None:
        run = runs.get(claim["run_id"])
        if (run is None or type(claim["fence"]) is not int or claim["fence"] != run_meta[claim["run_id"]]["attempt_number"]
                or claim["fence"] != max(fences) or claim["owner"] != run["runner"]
                or claim["request_key"] != claims[claim["run_id"]]["request_key"]
                or type(claim["expires_at"]) not in {int, float} or not math.isfinite(claim["expires_at"])
                or claim["state"] not in {"active", "released"}):
            raise EvidenceError("claim_attempt_fence_disagreement")
        if claim["state"] == "active":
            reconciliation.append("active_legacy_lease" if claim["expires_at"] > now else "expired_claim_requires_reconciliation")
    for attempt in attempts.values():
        step = steps.get(attempt["step_db_row_id"])
        if (attempt["case_run_id"] not in runs or step is None or step["step_id"] != attempt["step_id"]
                or step["idx"] != attempt["idx"] or attempt["status"] not in {"running", "completed", "blocked", "failed", "skipped"}):
            raise EvidenceError("foreign_or_unbound_step_attempt")
        if attempt["status"] == "running":
            reconciliation.append("unfinished_step_attempt")
        _decode(attempt["metadata_json"])
    slots = {row["name"]: row for row in value["slots"]}
    if len(slots) != len(value["slots"]) or set(slots) != set(contract["slot_names"]):
        raise EvidenceError("incomplete_or_foreign_slot_inventory")
    values = {name: _decode(row["value"], optional=True) for name, row in slots.items()}
    roots = value["root_intent"].get("root_inputs", {}) if type(value["root_intent"]) is dict else {}
    for name in contract["root_inputs"]:
        if slots[name]["value"] is not None and (name not in roots or values[name] != roots[name] or slots[name]["agent_run_id"] is not None):
            raise EvidenceError("root_slot_provenance_disagreement")
    for spec in contract["steps"]:
        step = by_name[spec["step_id"]]
        if step["status"] not in {"PENDING", "READY", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"}:
            raise EvidenceError("unsupported_ordinal_step_state")
        if step["status"] == "RUNNING":
            reconciliation.append("running_step_requires_reconciliation")
        output = _decode(step["result_json"], optional=True)
        state = _decode(step["runtime_state_json"], optional=True)
        produced = {name: values[name] for name in spec["output_variables"] if slots[name]["value"] is not None}
        if step["status"] in {"PENDING", "READY"}:
            if output is not None or state is not None or produced or step["completed_at"] is not None or step["runtime_updated_at"] is not None:
                reasons.append("unfenced_progress_or_output_without_terminal_attempt")
            continue
        if step["status"] == "SKIPPED":
            reasons.append("ordinal_skip_has_no_success_receipt")
            continue
        if step["status"] == "RUNNING":
            continue
        if (type(output) is not dict or set(output) != set(spec["output_variables"])
                or type(state) is not dict or state.get("status") != step["status"].lower()
                or state.get("committed_outputs") != spec["output_variables"]):
            reasons.append("ordinal_terminal_label_without_committed_output_evidence")
            continue
        selected = {slots[name]["agent_run_id"] for name in produced}
        if len(selected) != 1 or None in selected:
            reasons.append("output_missing_attempt_provenance")
            continue
        attempt = attempts.get(next(iter(selected)))
        if attempt is None or attempt["step_db_row_id"] != step["id"] or attempt["status"] != step["status"].lower() or attempt["ended_at"] is None:
            reasons.append("output_attempt_receipt_disagreement")
            continue
        if (step["completed_at"] is None or state.get("completed_at") != step["completed_at"]
                or step["runtime_updated_at"] != step["completed_at"] or attempt["ended_at"] != step["completed_at"]
                or any(slots[name]["filled_at"] != step["completed_at"] or slots[name]["produced_at"] != step["completed_at"] for name in produced)):
            reasons.append("output_commit_timestamp_disagreement")
            continue
        if any(output[name] != produced.get(name) or not _matches(contract["variables"][name]["type"], output[name]) for name in output):
            reasons.append("output_slot_type_or_digest_disagreement")
            continue
        retained.append({"step_id": step["step_id"], "step_attempt_id": attempt["id"], "legacy_run_id": attempt["case_run_id"],
            "fence": run_meta[attempt["case_run_id"]]["attempt_number"], "outcome": step["status"].lower(),
            "output_digest": digest(output), "variables": sorted(output), "native_receipt_verified": False,
            "carry_forward_allowed": False})
    effect_refs = []
    from services.cases.execution_contract import profile_for
    handlers = profile_for(contract)["handlers"]
    for effect in value["effects"]:
        attempt = attempts.get(effect["step_run_id"])
        if (attempt is None or effect["run_id"] != attempt["case_run_id"] or effect["operation"] not in {"transcribe", "write_status", "daily_note"}
                or handlers[attempt["step_id"]]["handler"] != effect["operation"]
                or effect["id"] != hashlib.sha256(f"legacy-effect.v1:{effect['run_id']}:{effect['step_run_id']}:{effect['operation']}".encode()).hexdigest()
                or effect["state"] not in {"unknown", "observed", "committed"}):
            raise EvidenceError("effect_inventory_binding_mismatch")
        evidence = _decode(effect["evidence_json"])
        if type(evidence) is not dict or (effect["state"] != "unknown" and not evidence):
            raise EvidenceError("effect_observation_evidence_missing")
        if effect["state"] in {"unknown", "observed"}:
            reconciliation.append("external_effect_outcome_requires_reconciliation")
        elif attempt["status"] not in {"completed", "failed"}:
            raise EvidenceError("effect_commit_without_terminal_attempt")
        effect_refs.append({"effect_id": effect["id"], "operation": effect["operation"], "state": effect["state"], "evidence_digest": digest(evidence)})
    for attempt in attempts.values():
        operation = handlers[attempt["step_id"]]["handler"]
        if operation in {"transcribe", "write_status", "daily_note"} and attempt["status"] in {"completed", "failed"}:
            if not any(effect["step_run_id"] == attempt["id"] and effect["state"] == "committed" for effect in value["effects"]):
                reconciliation.append("terminal_effect_step_missing_committed_inventory")
    spans = {row["id"]: row for row in value["spans"]}
    def linked(row):
        run_id, attempt_id = row["case_run_id"], row.get("step_run_id")
        span_id = row.get("span_id", row.get("parent_span_id"))
        return (run_id in runs
            and (attempt_id is None or (attempt_id in attempts and attempts[attempt_id]["case_run_id"] == run_id))
            and (span_id is None or (span_id in spans and spans[span_id]["case_run_id"] == run_id)))
    artifact_refs = []
    for artifact in value["artifacts"]:
        if not linked(artifact):
            raise EvidenceError("foreign_artifact_provenance")
        if artifact["sha256"] is not None and not _hash(artifact["sha256"]):
            raise EvidenceError("malformed_retained_artifact_hash")
        if artifact["size_bytes"] is not None and (type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 0):
            raise EvidenceError("malformed_artifact_size")
        _decode(artifact["metadata_json"])
        artifact_refs.append({"artifact_id": artifact["id"], "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"], "bytes_verified": False})
    for event in value["events"]:
        if not linked(event):
            raise EvidenceError("foreign_event_provenance")
        _decode(event["metadata_json"])
    for span in value["spans"]:
        if not linked(span) or span["parent_span_id"] == span["id"]:
            raise EvidenceError("foreign_span_provenance")
        _decode(span["metadata_json"])
    for log in value["logs"]:
        _decode(log["metadata_json"])
        if log["type"] == "legacy_attempt" and not runs:
            reconciliation.append("claim_log_without_attempt_inventory")
    for audit in value["model_task_audits"]:
        if audit["step_id"] not in by_name or (audit["step_db_row_id"] is not None and audit["step_db_row_id"] != by_name[audit["step_id"]]["id"]):
            raise EvidenceError("foreign_audit_provenance")
        _decode(audit["reference_json"]); _decode(audit["audit_record_json"])
    return reasons, reconciliation, retained, effect_refs, artifact_refs


def assess_legacy_evidence(snapshot, *, expected_evidence_sha256=None, now=None):
    result = {"schema": "hub.legacy-migration-assessment.v1", "case_id": None, "eligible": False,
              "mode": "read_only", "disposition": "read_only_unsupported", "reason": "malformed_legacy_snapshot", "reasons": [],
              "retained_outputs": [], "effects": [], "artifacts": [], "production_ready": False, "migration_authorized": False,
              "operator_review_required": True, "reassessment_before_admission_required": True}
    try:
        if type(snapshot) not in {dict, _CollectedEvidence} or set(snapshot) != ROOT_FIELDS or snapshot["schema"] != EVIDENCE_SCHEMA:
            raise EvidenceError("closed_legacy_evidence_required")
        if len(canonical(snapshot)) > MAX_EVIDENCE_BYTES:
            raise EvidenceError("oversized_legacy_snapshot")
        selected_pin = expected_evidence_sha256
        if selected_pin is None and type(snapshot) is _CollectedEvidence:
            selected_pin = snapshot.evidence_sha256
        if not _hash(selected_pin):
            raise EvidenceError("source_snapshot_not_independently_pinned")
        if digest(snapshot) != selected_pin:
            raise EvidenceError("source_snapshot_pin_mismatch")
        _timestamp(snapshot["captured_at"])
        provenance = snapshot["provenance"]
        if (type(provenance) is not dict or set(provenance) != {"kind", "database_sha256", "snapshot_sha256"}
                or provenance["kind"] != "frozen_sqlite_copy" or not _hash(provenance["database_sha256"])
                or provenance["snapshot_sha256"] != _snapshot_digest(snapshot)):
            raise EvidenceError("snapshot_provenance_hash_mismatch")
        _validate_rows(snapshot)
        result["case_id"] = snapshot["case"]["id"]
        source_hash = _validate_contract(snapshot)
        root_hash, root_problem = None, None
        try:
            root_hash = _root_intent(snapshot)
        except EvidenceError as error:
            root_problem = str(error)
        clock = time.time() if now is None else now
        if type(clock) not in {int, float} or not math.isfinite(clock) or clock <= 0:
            raise EvidenceError("invalid_assessment_clock")
        if _timestamp(snapshot["captured_at"]) > clock:
            raise EvidenceError("future_dated_snapshot")
        reasons, reconciliation, retained, effects, artifacts = _history(snapshot, clock)
        result.update(retained_outputs=retained, effects=effects, artifacts=artifacts,
            evidence={"source_snapshot_sha256": selected_pin, "snapshot_sha256": provenance["snapshot_sha256"], "database_sha256": provenance["database_sha256"],
                      "frozen_contract_hash": snapshot["frozen_contract_hash"], "process_source_hash": source_hash, "root_intent_hash": root_hash},
            inventory={name: len(snapshot[name]) for name in ROW_KEYS})
        status = snapshot["case"]["status"]
        if status not in {"OPEN", "READY", "IN_PROGRESS", "RUNNING", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED"}:
            raise EvidenceError("unsupported_case_status")
        if reconciliation:
            result.update(disposition="reconciliation_required", reason="reconcile_legacy_execution_first")
        elif status in {"COMPLETED", "FAILED", "CANCELLED"}:
            result.update(disposition="read_only_terminal", reason="terminal_history_requires_retained_evidence")
        elif reasons:
            result.update(reason="unsupported_ordinal_or_unvalidated_output_history")
        elif retained or artifacts:
            result.update(disposition="compatibility_revision_required", reason="explicit_output_compatibility_revision_required")
        elif snapshot["runs"] or snapshot["step_runs"] or snapshot["effects"] or snapshot["claim"] is not None:
            result.update(disposition="reconciliation_required", reason="prior_execution_requires_operator_disposition")
        elif root_problem:
            result.update(reason="missing_intake_evidence")
        elif status not in {"OPEN", "READY"}:
            result.update(reason="nonroot_legacy_status_requires_review")
        else:
            result.update(eligible=True, mode="create_new_run", disposition="new_root_run_candidate", reason="new_pinned_run_from_explicit_root_intent")
        result["reasons"] = list(dict.fromkeys([result["reason"], *reconciliation, *reasons, *([root_problem] if root_problem else [])]))
        return result
    except (EvidenceError, ValueError, TypeError, KeyError, OverflowError, RecursionError, UnicodeError, OSError):
        # Fixed diagnostics never expose source prose, inputs, outputs or SQL.
        import sys
        error = sys.exc_info()[1]
        reason = str(error) if isinstance(error, EvidenceError) else "malformed_or_unsupported_legacy_evidence"
        result.update(eligible=False, mode="read_only", disposition="read_only_unsupported", reason=reason, reasons=[reason])
        return result


def _file_digest(path):
    if path.stat().st_size > MAX_DATABASE_BYTES:
        raise EvidenceError("database_exceeds_inventory_bound")
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1_048_576), b""):
            h.update(block)
    return h.hexdigest()


def _no_journal(path):
    if any(Path(str(path)+suffix).exists() and Path(str(path)+suffix).stat().st_size for suffix in ("-wal", "-journal")):
        raise EvidenceError("use_frozen_journal_free_sqlite_copy")


def collect_legacy_evidence(database, *, case_ids=None, max_cases=1000):
    """Return closed evidence from a stable backup; never modify even sidecars."""
    path = Path(database)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or type(max_cases) is not int or not 1 <= max_cases <= 10_000:
        raise EvidenceError("explicit_frozen_database_and_bounds_required")
    _no_journal(path); before = _file_digest(path)
    connection = sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1", uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    snapshots = []
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if not set(TABLE_FIELDS) <= tables or any(name.startswith("legacy_") and name not in TABLE_FIELDS for name in tables):
            raise EvidenceError("incomplete_or_unknown_legacy_inventory_schema")
        for table, fields in TABLE_FIELDS.items():
            if {row[1] for row in connection.execute(f"PRAGMA table_info({table})")} != set(fields):
                raise EvidenceError("unreviewed_legacy_inventory_columns")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise EvidenceError("inconsistent_foreign_key_inventory")
        rows = connection.execute("SELECT * FROM cases ORDER BY id LIMIT ?", (max_cases+1,)).fetchall()
        if len(rows) > max_cases:
            raise EvidenceError("case_inventory_exceeds_bound")
        if case_ids is not None and (type(case_ids) not in {list, tuple} or any(not _id(item) for item in case_ids) or len(set(case_ids)) != len(case_ids)):
            raise EvidenceError("invalid_selected_case_ids")
        selected = set(case_ids) if case_ids is not None else {row["id"] for row in rows}
        if not selected <= {row["id"] for row in rows}:
            raise EvidenceError("selected_case_not_found")
        for raw in rows:
            if raw["id"] not in selected:
                continue
            case = dict(raw)
            value = {"schema": EVIDENCE_SCHEMA, "captured_at": datetime.now(timezone.utc).isoformat(), "case": case}
            try:
                contract = _decode(case["contract_json"])
            except (ValueError, TypeError, UnicodeError):
                contract = None
            value.update(frozen_contract=contract, frozen_contract_hash=digest(contract) if isinstance(contract, dict) else None, root_intent=None)
            try:
                packet = _decode(case["dispatch_packet_json"], optional=True)
                if type(packet) is dict and type(packet.get("initial_context")) is dict:
                    value["root_intent"] = {"source": "case.dispatch_packet_json.initial_context", "case_id": case["id"],
                        "queue_message_id": case["queue_message_id"], "root_inputs": packet["initial_context"], "packet_sha256": digest(packet)}
            except (ValueError, TypeError, UnicodeError):
                pass
            for name, table in ROW_KEYS.items():
                if table == "execution_spans":
                    records = connection.execute("SELECT * FROM execution_spans WHERE case_run_id IN (SELECT id FROM case_runs WHERE case_id=?) ORDER BY id LIMIT ?", (case["id"], MAX_RECORDS+1)).fetchall()
                else:
                    order = "idx,id" if table == "case_steps" else "request_key" if table == "legacy_claim_requests" else "id"
                    records = connection.execute(f"SELECT * FROM {table} WHERE case_id=? ORDER BY {order} LIMIT ?", (case["id"], MAX_RECORDS+1)).fetchall()
                if len(records) > MAX_RECORDS:
                    raise EvidenceError("record_inventory_exceeds_bound")
                value[name] = [dict(record) for record in records]
            claim = connection.execute("SELECT * FROM legacy_execution_claims WHERE case_id=?", (case["id"],)).fetchone()
            value["claim"] = dict(claim) if claim else None
            value["provenance"] = {"kind": "frozen_sqlite_copy", "database_sha256": before, "snapshot_sha256": _snapshot_digest(value)}
            if len(canonical(value)) > MAX_EVIDENCE_BYTES:
                raise EvidenceError("snapshot_inventory_exceeds_bound")
            snapshots.append(value)
        connection.rollback()
    except sqlite3.DatabaseError:
        raise EvidenceError("legacy_inventory_storage_unavailable") from None
    finally:
        connection.close()
    _no_journal(path)
    if _file_digest(path) != before:
        raise EvidenceError("legacy_snapshot_changed_during_inventory")
    return [_CollectedEvidence(value) for value in snapshots]


def assess_inventory(snapshots, *, expected_evidence_sha256=None, now=None):
    if type(snapshots) is not list or len(snapshots) > 10_000:
        raise EvidenceError("bounded_inventory_required")
    if expected_evidence_sha256 is not None and (type(expected_evidence_sha256) is not dict
            or any(not _id(key) or not _hash(value) for key, value in expected_evidence_sha256.items())):
        raise EvidenceError("invalid_independent_inventory_pins")
    pins = expected_evidence_sha256 or {}
    assessments = [assess_legacy_evidence(item,
        expected_evidence_sha256=pins.get(item.get("case", {}).get("id")) if type(item) in {dict, _CollectedEvidence} and type(item.get("case")) is dict else None,
        now=now) for item in snapshots]
    ids = [row["case_id"] for row in assessments if row["case_id"] is not None]
    if len(ids) != len(set(ids)):
        raise EvidenceError("duplicate_case_inventory")
    return {"schema": "hub.legacy-migration-inventory.v1", "production_ready": False, "migration_authorized": False, "writes": 0,
            "snapshot_count": len(snapshots), "assessments": assessments,
            "counts": {mode: sum(row["disposition"] == mode for row in assessments) for mode in
                ("new_root_run_candidate", "reconciliation_required", "read_only_unsupported", "read_only_terminal", "compatibility_revision_required")}}
