from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient


VALID_PROCESS = """# Demo process

## What this process does

Compiles a minimal process contract for tests.

---

## Steps

### Step 1 — Load source

**Input:** `request_id`

**Processing:** Load and normalize the source request.

**Output (process state):**
```json
{
  "source_text": "...",
  "summary": "..."
}
```

---

### Step 2 — Save file

**Input:** `source_text`, `summary`

**Resource:** `vault`

**Processing:** Write the rendered file to the vault.

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `request_id` | string | Unique request identifier |
| `source_text` | string | Normalized source text from Step 1 |
| `summary` | string | Short summary produced by Step 1 |
"""

PATH_STATE_PROCESS = """# Path state process

## What this process does

Compiles a contract where a path-typed variable still creates a graph edge.

---

## Steps

### Step 1 — Write review

**Input:** `request_id`

**Processing:** Write the initial review note path.

**Output (process state):**
```json
{
  "review_note_path": "~/vault/review.md"
}
```

---

### Step 2 — Resolve review

**Input:** `review_note_path`

**Resource:** `vault`

**Processing:** Update the review note in place.

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `request_id` | string | Unique request identifier |
| `review_note_path` | string (path) | Vault path to the generated review note |
"""


class CasesContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["CASES_DB_PATH"] = os.path.join(self.tmpdir.name, "cases.db")
        sys.modules.pop("services.cases.main", None)
        module = importlib.import_module("services.cases.main")
        self.module = importlib.reload(module)
        self.client_context = TestClient(self.module.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.tmpdir.cleanup()

    def create_case(self) -> dict:
        response = self.client.post(
            "/cases",
            json={
                "queue_message_id": "msg_1",
                "process_name": "demo",
                "process_path": "demo-process",
                "process_source": VALID_PROCESS,
                "title": "demo",
                "objective": "demo objective",
                "sender": "tester",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def create_case_again_with_same_queue_message(self) -> dict:
        response = self.client.post(
            "/cases",
            json={
                "queue_message_id": "msg_1",
                "process_name": "demo",
                "process_path": "demo-process",
                "process_source": VALID_PROCESS,
                "title": "demo retry",
                "objective": "demo objective retry",
                "sender": "tester",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def create_case_with_dispatch_packet(self) -> dict:
        response = self.client.post(
            "/cases",
            json={
                "queue_message_id": "msg_with_packet",
                "process_name": "demo",
                "process_path": "demo-process",
                "process_source": VALID_PROCESS,
                "title": "demo with packet",
                "objective": "demo objective",
                "sender": "tester",
                "dispatch_packet_json": {"initial_context": {"request_id": "req_from_packet"}},
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def create_path_state_case(self) -> dict:
        response = self.client.post(
            "/cases",
            json={
                "queue_message_id": "msg_2",
                "process_name": "path-state-demo",
                "process_path": "path-state-process",
                "process_source": PATH_STATE_PROCESS,
                "title": "path state demo",
                "objective": "path state objective",
                "sender": "tester",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def model_task_audit_record(self, case_id: str, step: dict) -> dict:
        return {
            "case_id": case_id,
            "step_id": step["step_id"],
            "step_db_row_id": step["id"],
            "kanban_task_id": "ktask_step_1",
            "hermes_run_id": "run_123",
            "profile": "frank",
            "provider": "openai-codex",
            "model": "gpt-5.3-codex",
            "hermes_home": "/hub/.hermes",
            "workspace": "scratch",
            "prompt_artifact": "dir:/hub/.hermes/frank_execution/case_1/audit/prompt.md",
            "prompt_sha256": "sha256:prompt",
            "task_artifact_sha256": "sha256:task",
            "final_response_artifact": "dir:/hub/.hermes/frank_execution/case_1/audit/final.md",
            "final_response_sha256": "sha256:final",
            "tool_calls_artifact": "dir:/hub/.hermes/frank_execution/case_1/audit/tool-calls.redacted.json",
            "tool_calls_sha256": "sha256:tools",
            "completion_metadata_artifact": "dir:/hub/.hermes/frank_execution/case_1/audit/completion-metadata.json",
            "completion_metadata_sha256": "sha256:metadata",
            "outcome": "completed",
        }

    def test_case_creation_precreates_contract_steps_and_slots(self) -> None:
        created = self.create_case()
        self.assertEqual([step["step_id"] for step in created["steps"]], ["step_1", "step_2"])
        self.assertTrue(all(step["id"].startswith("step_") for step in created["steps"]))
        self.assertEqual(created["progress"]["completed_step_count"], 0)
        detail = self.client.get(f"/cases/{created['case_id']}")
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(payload["case"]["status"], "OPEN")
        self.assertEqual(payload["contract"]["slot_names"], ["request_id", "source_text", "summary"])
        self.assertEqual([slot["name"] for slot in payload["slots"]], ["request_id", "source_text", "summary"])
        self.assertEqual([step["step_id"] for step in payload["steps"]], ["step_1", "step_2"])
        self.assertEqual([step["status"] for step in payload["steps"]], ["PENDING", "PENDING"])

    def test_case_creation_is_idempotent_by_queue_message_id(self) -> None:
        created = self.create_case()
        retried = self.create_case_again_with_same_queue_message()

        self.assertEqual(retried["case_id"], created["case_id"])
        self.assertEqual(retried["reused"], True)

        listed = self.client.get("/cases?limit=20")
        self.assertEqual(listed.status_code, 200)
        cases = listed.json()["cases"]
        matching = [case for case in cases if case["queue_message_id"] == "msg_1"]
        self.assertEqual(len(matching), 1)

    def test_case_list_omits_heavy_process_fields_by_default(self) -> None:
        self.create_case_with_dispatch_packet()

        listed = self.client.get("/cases?limit=20")

        self.assertEqual(listed.status_code, 200)
        case = next(case for case in listed.json()["cases"] if case["queue_message_id"] == "msg_with_packet")
        self.assertNotIn("process_source", case)
        self.assertNotIn("contract_json", case)
        self.assertNotIn("dispatch_packet_json", case)

    def test_case_list_can_include_heavy_process_fields_for_legacy_callers(self) -> None:
        self.create_case_with_dispatch_packet()

        listed = self.client.get("/cases?limit=20&include_heavy=true")

        self.assertEqual(listed.status_code, 200)
        case = next(case for case in listed.json()["cases"] if case["queue_message_id"] == "msg_with_packet")
        self.assertIn("process_source", case)
        self.assertIn("contract_json", case)
        self.assertIn("dispatch_packet_json", case)

    def test_unknown_slot_write_is_rejected_and_logged(self) -> None:
        created = self.create_case()
        response = self.client.post(
            f"/cases/{created['case_id']}/slots",
            json={"name": "rogue_slot", "value": "oops"},
        )
        self.assertEqual(response.status_code, 422)
        detail = self.client.get(f"/cases/{created['case_id']}").json()
        self.assertTrue(any("rogue_slot" in log["message"] for log in detail["logs"]))

    def test_model_task_audit_upsert_persists_canonical_record_and_reuses_reference(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        detail = self.client.get(f"/cases/{case_id}").json()
        step = detail["steps"][0]
        record = self.model_task_audit_record(case_id, step)

        first = self.client.post(f"/cases/{case_id}/model-task-audits", json={"step_id": step["step_id"], "audit_record": record})
        self.assertEqual(first.status_code, 201)
        first_ref = first.json()
        self.assertEqual(first_ref["upsert_status"], "created")
        self.assertEqual(first_ref["case_id"], case_id)
        self.assertEqual(first_ref["step_id"], step["step_id"])
        self.assertEqual(first_ref["step_db_row_id"], step["id"])
        self.assertEqual(first_ref["artifact_hashes"]["prompt"], "sha256:prompt")

        second = self.client.post(f"/cases/{case_id}/model-task-audits", json={"step_id": step["step_id"], "audit_record": record})
        self.assertEqual(second.status_code, 201)
        second_ref = second.json()
        self.assertEqual(second_ref["upsert_status"], "reused")
        self.assertEqual(second_ref["audit_record_id"], first_ref["audit_record_id"])

        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertEqual(len(detail["model_task_audits"]), 1)
        persisted = detail["model_task_audits"][0]
        self.assertEqual(persisted["reference_json"]["audit_record_id"], first_ref["audit_record_id"])
        self.assertNotIn("prompt text", str(persisted))

    def test_model_task_audit_rejects_secret_bearing_payload(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        detail = self.client.get(f"/cases/{case_id}").json()
        step = detail["steps"][0]
        record = self.model_task_audit_record(case_id, step)
        record["env"] = {"OPENAI_API_KEY": "sk-should-not-persist"}

        response = self.client.post(f"/cases/{case_id}/model-task-audits", json={"step_id": step["step_id"], "audit_record": record})
        self.assertEqual(response.status_code, 422)
        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertEqual(detail["model_task_audits"], [])

    def test_model_task_audit_strips_raw_payload_fields_before_persisting(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        detail = self.client.get(f"/cases/{case_id}").json()
        step = detail["steps"][0]
        record = self.model_task_audit_record(case_id, step)
        record["prompt_text"] = "raw prompt text must not persist"
        record["final_response_text"] = "raw response text must not persist"
        record["messages"] = [{"role": "user", "content": "raw message"}]

        response = self.client.post(f"/cases/{case_id}/model-task-audits", json={"step_id": step["step_id"], "audit_record": record})
        self.assertEqual(response.status_code, 201)
        detail = self.client.get(f"/cases/{case_id}").json()
        persisted = detail["model_task_audits"][0]["audit_record_json"]
        self.assertNotIn("prompt_text", persisted)
        self.assertNotIn("final_response_text", persisted)
        self.assertNotIn("messages", persisted)
        self.assertNotIn("raw prompt text", str(detail["model_task_audits"]))

    def test_model_task_audit_ids_are_case_scoped_for_reused_hermes_run_ids(self) -> None:
        first = self.create_case()
        second = self.create_case_again_with_same_queue_message()
        # Use a genuinely separate case to prove global primary keys do not collide.
        second_response = self.client.post(
            "/cases",
            json={
                "queue_message_id": "msg_second_case",
                "process_name": "demo",
                "process_path": "demo-process",
                "process_source": VALID_PROCESS,
                "title": "second demo",
                "objective": "second objective",
                "sender": "tester",
            },
        )
        self.assertEqual(second_response.status_code, 201)
        second = second_response.json()

        first_detail = self.client.get(f"/cases/{first['case_id']}").json()
        second_detail = self.client.get(f"/cases/{second['case_id']}").json()
        first_record = self.model_task_audit_record(first["case_id"], first_detail["steps"][0])
        second_record = self.model_task_audit_record(second["case_id"], second_detail["steps"][0])
        second_record["hermes_run_id"] = first_record["hermes_run_id"]

        first_ref = self.client.post(f"/cases/{first['case_id']}/model-task-audits", json={"step_id": first_detail["steps"][0]["step_id"], "audit_record": first_record})
        second_ref = self.client.post(f"/cases/{second['case_id']}/model-task-audits", json={"step_id": second_detail["steps"][0]["step_id"], "audit_record": second_record})
        self.assertEqual(first_ref.status_code, 201)
        self.assertEqual(second_ref.status_code, 201)
        self.assertNotEqual(first_ref.json()["audit_record_id"], second_ref.json()["audit_record_id"])

    def test_unknown_step_output_is_rejected_and_logged(self) -> None:
        created = self.create_case()
        detail = self.client.get(f"/cases/{created['case_id']}").json()
        step_db_id = detail["steps"][0]["id"]
        response = self.client.put(
            f"/cases/{created['case_id']}/steps/{step_db_id}",
            json={"status": "COMPLETED", "result_json": {"rogue_output": "oops"}},
        )
        self.assertEqual(response.status_code, 422)
        detail = self.client.get(f"/cases/{created['case_id']}").json()
        self.assertTrue(any("rogue_output" in log["message"] for log in detail["logs"]))

    def test_root_slot_prefill_makes_first_step_ready(self) -> None:
        created = self.create_case()
        response = self.client.post(
            f"/cases/{created['case_id']}/slots",
            json={"name": "request_id", "value": "req_123"},
        )
        self.assertEqual(response.status_code, 200)

        detail = self.client.get(f"/cases/{created['case_id']}").json()
        self.assertEqual(detail["case"]["status"], "OPEN")
        self.assertEqual([step["status"] for step in detail["steps"]], ["READY", "PENDING"])
        self.assertEqual(detail["progress"]["ready_steps"], [detail["steps"][0]["id"]])

    def test_slot_rewrite_accepts_identical_value_as_idempotent_retry(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        first = self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        second = self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {"ok": True, "idempotent": True})

    def test_slot_rewrite_rejects_changed_value(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        response = self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_999"})
        self.assertEqual(response.status_code, 409)
        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertTrue(any("Rejected slot rewrite: request_id" == log["message"] for log in detail["logs"]))

    def test_output_slots_auto_complete_step_and_ready_downstream(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        step_one_id = self.client.get(f"/cases/{case_id}").json()["steps"][0]["id"]

        running = self.client.put(
            f"/cases/{case_id}/steps/{step_one_id}",
            json={"status": "RUNNING"},
        )
        self.assertEqual(running.status_code, 200)

        update = self.client.put(
            f"/cases/{case_id}/steps/{step_one_id}",
            json={"agent_run_id": "run_123", "result_json": {"source_text": "hello", "summary": "brief"}},
        )
        self.assertEqual(update.status_code, 200)

        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertEqual(detail["steps"][0]["status"], "COMPLETED")
        self.assertEqual(detail["steps"][0]["completed_at"] is not None, True)
        self.assertEqual(detail["steps"][1]["status"], "READY")
        self.assertEqual(detail["case"]["status"], "IN_PROGRESS")
        self.assertEqual(detail["progress"]["completed_step_count"], 1)
        self.assertEqual(detail["progress"]["completed_steps"], [step_one_id])

        slots = {slot["name"]: slot for slot in detail["slots"]}
        self.assertEqual(slots["source_text"]["agent_run_id"], "run_123")
        self.assertEqual(slots["summary"]["agent_run_id"], "run_123")

    def test_terminal_step_completion_updates_case_completion(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        detail = self.client.get(f"/cases/{case_id}").json()
        step_one_id = detail["steps"][0]["id"]
        step_two_id = detail["steps"][1]["id"]

        self.client.put(
            f"/cases/{case_id}/steps/{step_one_id}",
            json={"status": "RUNNING", "result_json": {"source_text": "hello", "summary": "brief"}},
        )
        finish = self.client.put(
            f"/cases/{case_id}/steps/{step_two_id}",
            json={"status": "COMPLETED"},
        )
        self.assertEqual(finish.status_code, 200)

        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertEqual([step["status"] for step in detail["steps"]], ["COMPLETED", "COMPLETED"])
        self.assertEqual(detail["case"]["status"], "COMPLETED")
        self.assertIsNotNone(detail["case"]["completed_at"])

    def test_direct_slot_write_with_agent_run_id_can_complete_step(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        step_one_id = self.client.get(f"/cases/{case_id}").json()["steps"][0]["id"]
        self.client.put(f"/cases/{case_id}/steps/{step_one_id}", json={"status": "RUNNING"})

        for slot_name, value in (("source_text", "hello"), ("summary", "brief")):
            response = self.client.post(
                f"/cases/{case_id}/slots",
                json={"name": slot_name, "value": value, "agent_run_id": "run_456"},
            )
            self.assertEqual(response.status_code, 200)

        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertEqual(detail["steps"][0]["status"], "COMPLETED")
        self.assertEqual(detail["steps"][1]["status"], "READY")
        self.assertEqual(detail["case"]["status"], "IN_PROGRESS")
        slots = {slot["name"]: slot for slot in detail["slots"]}
        self.assertEqual(slots["source_text"]["agent_run_id"], "run_456")
        self.assertEqual(slots["summary"]["agent_run_id"], "run_456")

    def test_legacy_status_aliases_are_normalized(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        detail = self.client.get(f"/cases/{case_id}").json()
        step_one_id = detail["steps"][0]["id"]
        step_two_id = detail["steps"][1]["id"]

        self.client.put(
            f"/cases/{case_id}/steps/{step_one_id}",
            json={"status": "RUNNING", "result_json": {"source_text": "hello", "summary": "brief"}},
        )
        response = self.client.put(
            f"/cases/{case_id}/steps/{step_two_id}",
            json={"status": "SUCCESS"},
        )
        self.assertEqual(response.status_code, 200)
        self.client.put(f"/cases/{case_id}/status", json={"status": "COMPLETE"})

        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertEqual(detail["steps"][1]["status"], "COMPLETED")
        self.assertEqual(detail["case"]["status"], "COMPLETED")

    def test_get_case_returns_persisted_contract_snapshot(self) -> None:
        created = self.create_case()
        detail = self.client.get(f"/cases/{created['case_id']}")
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(payload["contract"]["process_path"], "demo-process")
        self.assertEqual(payload["case"]["process_hash"], payload["contract"]["process_hash"])

    def test_step_runtime_state_round_trips_and_marks_progress_running(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        step_id = self.client.get(f"/cases/{case_id}").json()["steps"][0]["id"]

        response = self.client.put(
            f"/cases/{case_id}/steps/{step_id}/runtime-state",
            json={
                "runtime_state_json": {
                    "status": "active",
                    "agent_run_id": "run_123",
                    "tasks": [
                        {"id": "task_1", "label": "load review", "status": "in_progress"},
                    ],
                    "current_focus": "task_1",
                    "retry_count": 0,
                }
            },
        )
        self.assertEqual(response.status_code, 200)

        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertEqual(detail["steps"][0]["runtime_state_json"]["status"], "active")
        self.assertEqual(detail["steps"][0]["runtime_state_json"]["agent_run_id"], "run_123")
        self.assertEqual(detail["progress"]["running_steps"], [step_id])

    def test_complete_step_outputs_requires_exact_declared_keys_and_types(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        step_id = self.client.get(f"/cases/{case_id}").json()["steps"][0]["id"]

        wrong_type = self.client.post(
            f"/cases/{case_id}/steps/{step_id}/complete-outputs",
            json={
                "outputs_json": {"source_text": "hello", "summary": 123},
                "agent_run_id": "run_1",
            },
        )
        self.assertEqual(wrong_type.status_code, 422)

        missing_key = self.client.post(
            f"/cases/{case_id}/steps/{step_id}/complete-outputs",
            json={
                "outputs_json": {"source_text": "hello"},
                "agent_run_id": "run_1",
            },
        )
        self.assertEqual(missing_key.status_code, 422)

    def test_complete_step_outputs_commits_outputs_and_completes_step(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        step_id = self.client.get(f"/cases/{case_id}").json()["steps"][0]["id"]

        self.client.put(
            f"/cases/{case_id}/steps/{step_id}/runtime-state",
            json={"runtime_state_json": {"status": "active", "agent_run_id": "run_123"}},
        )

        response = self.client.post(
            f"/cases/{case_id}/steps/{step_id}/complete-outputs",
            json={
                "outputs_json": {"source_text": "hello", "summary": "brief"},
                "agent_run_id": "run_123",
                "notes": ["step one finished"],
            },
        )
        self.assertEqual(response.status_code, 200)

        detail = self.client.get(f"/cases/{case_id}").json()
        self.assertEqual(detail["steps"][0]["status"], "COMPLETED")
        self.assertEqual(detail["steps"][1]["status"], "READY")
        self.assertEqual(detail["steps"][0]["runtime_state_json"]["status"], "completed")
        slots = {slot["name"]: slot for slot in detail["slots"]}
        self.assertEqual(slots["source_text"]["agent_run_id"], "run_123")
        self.assertEqual(slots["summary"]["agent_run_id"], "run_123")
        self.assertTrue(any(log["message"] == "step one finished" for log in detail["logs"]))

    def test_dispatch_packet_round_trips_through_case_snapshot(self) -> None:
        created = self.create_case_with_dispatch_packet()
        detail = self.client.get(f"/cases/{created['case_id']}")
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(
            payload["case"]["dispatch_packet_json"],
            {"initial_context": {"request_id": "req_from_packet"}},
        )

    def test_dispatch_packet_can_be_updated_after_case_creation(self) -> None:
        created = self.create_case()
        response = self.client.put(
            f"/cases/{created['case_id']}/dispatch-packet",
            json={"dispatch_packet_json": {"worker": {"executor": "sophia"}}},
        )
        self.assertEqual(response.status_code, 200)
        detail = self.client.get(f"/cases/{created['case_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["case"]["dispatch_packet_json"], {"worker": {"executor": "sophia"}})

    def test_case_status_can_be_marked_ready_independently_of_step_readiness(self) -> None:
        created = self.create_case()
        response = self.client.put(
            f"/cases/{created['case_id']}/status",
            json={"status": "READY"},
        )
        self.assertEqual(response.status_code, 200)
        detail = self.client.get(f"/cases/{created['case_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["case"]["status"], "READY")

    def test_case_running_alias_normalizes_to_in_progress(self) -> None:
        created = self.create_case()
        response = self.client.put(
            f"/cases/{created['case_id']}/status",
            json={"status": "RUNNING"},
        )
        self.assertEqual(response.status_code, 200)
        detail = self.client.get(f"/cases/{created['case_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["case"]["status"], "IN_PROGRESS")

        listed = self.client.get("/cases?status=RUNNING&limit=20")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([case["id"] for case in listed.json()["cases"]], [created["case_id"]])

    def test_path_typed_variables_still_persist_dag_edges(self) -> None:
        created = self.create_path_state_case()
        detail = self.client.get(f"/cases/{created['case_id']}")
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(
            payload["contract"]["dag_edges"],
            [{"from": 0, "to": 1, "label": "review_note_path", "is_skip": False, "variables": ["review_note_path"]}],
        )


if __name__ == "__main__":
    unittest.main()
