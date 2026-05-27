from __future__ import annotations

import importlib
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_cases_contract import VALID_PROCESS


class CasesObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.artifact_root = Path(self.tmpdir.name) / "frank_execution"
        self.artifact_root.mkdir()
        os.environ["CASES_DB_PATH"] = os.path.join(self.tmpdir.name, "cases.db")
        os.environ["FRANK_EXECUTION_ROOT"] = str(self.artifact_root)
        os.environ["CASES_MIRROR_ALLOWED_ROOTS"] = str(self.artifact_root)
        sys.modules.pop("services.cases.main", None)
        module = importlib.import_module("services.cases.main")
        self.module = importlib.reload(module)
        self.client_context = TestClient(self.module.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        os.environ.pop("FRANK_EXECUTION_ROOT", None)
        os.environ.pop("CASES_MIRROR_ALLOWED_ROOTS", None)
        self.tmpdir.cleanup()

    def create_case(self) -> dict:
        response = self.client.post(
            "/cases",
            json={
                "queue_message_id": "msg_observe_1",
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

    def test_case_run_step_span_event_and_artifact_contract(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        step = self.client.get(f"/cases/{case_id}").json()["steps"][0]

        run_response = self.client.post(
            f"/cases/{case_id}/runs",
            json={
                "runtime_mode": "native_case_pipeline",
                "runner": "frank.case_pipeline",
                "status": "running",
                "idempotency_key": f"native:{case_id}",
                "metadata": {"source": "test"},
            },
        )
        self.assertEqual(run_response.status_code, 201)
        case_run = run_response.json()
        self.assertEqual(case_run["status"], "running")
        self.assertEqual(case_run["metadata_json"], {"source": "test"})

        reused = self.client.post(
            f"/cases/{case_id}/runs",
            json={
                "runtime_mode": "native_case_pipeline",
                "runner": "frank.case_pipeline",
                "status": "running",
                "idempotency_key": f"native:{case_id}",
            },
        )
        self.assertEqual(reused.status_code, 201)
        self.assertEqual(reused.json()["id"], case_run["id"])
        self.assertEqual(reused.json()["reused"], True)

        step_run_response = self.client.post(
            f"/case-runs/{case_run['id']}/steps",
            json={
                "case_run_id": case_run["id"],
                "step_id": step["step_id"],
                "step_db_row_id": step["id"],
                "executor_type": "native",
                "status": "running",
                "idempotency_key": f"{case_run['id']}:{step['id']}",
                "metadata": {"model_backed": False},
            },
        )
        self.assertEqual(step_run_response.status_code, 201)
        step_run = step_run_response.json()
        self.assertEqual(step_run["executor_type"], "native")
        self.assertEqual(step_run["metadata_json"]["model_backed"], False)

        span_response = self.client.post(
            f"/case-runs/{case_run['id']}/spans",
            json={
                "case_run_id": case_run["id"],
                "step_run_id": step_run["id"],
                "name": "load assets",
                "status": "running",
            },
        )
        self.assertEqual(span_response.status_code, 201)
        span = span_response.json()

        event_response = self.client.post(
            f"/case-runs/{case_run['id']}/events",
            json={
                "case_run_id": case_run["id"],
                "step_run_id": step_run["id"],
                "span_id": span["id"],
                "type": "artifact.written",
                "severity": "info",
                "message": "asset manifest written",
                "metadata": {"artifact_role": "asset_manifest"},
            },
        )
        self.assertEqual(event_response.status_code, 201)
        event = event_response.json()
        self.assertEqual(event["type"], "artifact.written")
        self.assertEqual(event["metadata_json"]["artifact_role"], "asset_manifest")

        artifact_response = self.client.post(
            f"/case-runs/{case_run['id']}/artifacts",
            json={
                "case_run_id": case_run["id"],
                "step_run_id": step_run["id"],
                "span_id": span["id"],
                "role": "asset_manifest",
                "uri": "dir:/tmp/case/asset_manifest.json",
                "sha256": "abc123",
                "size_bytes": 12,
                "content_type": "application/json",
                "redaction_status": "not_applicable",
            },
        )
        self.assertEqual(artifact_response.status_code, 201)
        artifact = artifact_response.json()
        self.assertEqual(artifact["role"], "asset_manifest")

        runs = self.client.get(f"/cases/{case_id}/runs").json()["case_runs"]
        self.assertEqual([run["id"] for run in runs], [case_run["id"]])
        steps = self.client.get(f"/case-runs/{case_run['id']}/steps").json()["step_runs"]
        self.assertEqual([item["id"] for item in steps], [step_run["id"]])
        spans = self.client.get(f"/step-runs/{step_run['id']}/spans").json()["spans"]
        self.assertEqual([item["id"] for item in spans], [span["id"]])
        events = self.client.get(f"/step-runs/{step_run['id']}/events").json()["events"]
        self.assertEqual([item["id"] for item in events], [event["id"]])
        filtered_events = self.client.get(
            f"/step-runs/{step_run['id']}/events",
            params={"event_type": "artifact.written"},
        ).json()["events"]
        self.assertEqual([item["id"] for item in filtered_events], [event["id"]])
        case_run_events = self.client.get(f"/case-runs/{case_run['id']}/events").json()["events"]
        self.assertEqual([item["id"] for item in case_run_events], [event["id"]])
        artifacts = self.client.get(f"/step-runs/{step_run['id']}/artifacts").json()["artifacts"]
        self.assertEqual([item["id"] for item in artifacts], [artifact["id"]])
        case_run_artifacts = self.client.get(f"/case-runs/{case_run['id']}/artifacts").json()["artifacts"]
        self.assertEqual([item["id"] for item in case_run_artifacts], [artifact["id"]])
        detail_artifacts = self.client.get(f"/cases/{case_id}").json()["artifacts"]
        self.assertEqual([item["id"] for item in detail_artifacts], [artifact["id"]])

        markdown_path = self.artifact_root / "case_demo" / "artifacts" / "review.md"
        markdown_path.parent.mkdir(parents=True)
        markdown_path.write_text("# Review\n\nArtifact preview works.\n", encoding="utf-8")
        markdown_artifact_response = self.client.post(
            f"/case-runs/{case_run['id']}/artifacts",
            json={
                "case_run_id": case_run["id"],
                "step_run_id": step_run["id"],
                "span_id": span["id"],
                "role": "review_note",
                "uri": f"dir:{markdown_path}",
                "size_bytes": markdown_path.stat().st_size,
                "content_type": "text/markdown",
                "redaction_status": "not_applicable",
            },
        )
        self.assertEqual(markdown_artifact_response.status_code, 201)
        markdown_artifact = markdown_artifact_response.json()
        metadata_response = self.client.get(f"/execution-artifacts/{markdown_artifact['id']}")
        self.assertEqual(metadata_response.status_code, 200)
        self.assertEqual(metadata_response.json()["role"], "review_note")
        content_response = self.client.get(f"/execution-artifacts/{markdown_artifact['id']}/content")
        self.assertEqual(content_response.status_code, 200)
        self.assertIn("# Review", content_response.text)
        self.assertEqual(content_response.headers["content-type"].split(";")[0], "text/markdown")
        scoped_content_response = self.client.get(
            f"/case-runs/{case_run['id']}/artifacts/{markdown_artifact['id']}/content"
        )
        self.assertEqual(scoped_content_response.status_code, 200)
        self.assertIn("Artifact preview works", scoped_content_response.text)

        encoded_path = base64.urlsafe_b64encode(str(markdown_path).encode("utf-8")).decode("ascii").rstrip("=")
        mirror_content_response = self.client.get(f"/mirror/files/{encoded_path}/content")
        self.assertEqual(mirror_content_response.status_code, 200)
        self.assertIn("Artifact preview works", mirror_content_response.text)

        outside_path = Path(self.tmpdir.name) / "outside.md"
        outside_path.write_text("# Outside\n", encoding="utf-8")
        outside_artifact_response = self.client.post(
            f"/case-runs/{case_run['id']}/artifacts",
            json={
                "case_run_id": case_run["id"],
                "role": "outside_note",
                "uri": f"dir:{outside_path}",
                "content_type": "text/markdown",
                "redaction_status": "not_applicable",
            },
        )
        self.assertEqual(outside_artifact_response.status_code, 201)
        outside_artifact = outside_artifact_response.json()
        denied_response = self.client.get(f"/execution-artifacts/{outside_artifact['id']}/content")
        self.assertEqual(denied_response.status_code, 403)
        outside_encoded_path = base64.urlsafe_b64encode(str(outside_path).encode("utf-8")).decode("ascii").rstrip("=")
        mirror_denied_response = self.client.get(f"/mirror/files/{outside_encoded_path}/content")
        self.assertEqual(mirror_denied_response.status_code, 403)

    def test_board_projection_is_derived_from_cases_step_runs(self) -> None:
        created = self.create_case()
        case_id = created["case_id"]
        step = self.client.get(f"/cases/{case_id}").json()["steps"][0]
        case_run = self.client.post(
            f"/cases/{case_id}/runs",
            json={
                "runtime_mode": "native_case_pipeline",
                "runner": "frank.case_pipeline",
                "status": "running",
                "idempotency_key": f"native:{case_id}",
            },
        ).json()
        step_run = self.client.post(
            f"/case-runs/{case_run['id']}/steps",
            json={
                "case_run_id": case_run["id"],
                "step_id": step["step_id"],
                "step_db_row_id": step["id"],
                "executor_type": "native",
                "status": "completed",
                "idempotency_key": f"{case_run['id']}:{step['id']}",
            },
        ).json()

        board = self.client.get(f"/cases/{case_id}/board").json()

        self.assertEqual(board["source"], "cases")
        task = next(item for item in board["tasks"] if item["step_db_row_id"] == step["id"])
        self.assertEqual(task["step_run_id"], step_run["id"])
        self.assertEqual(task["status"], "completed")

    def test_board_projection_classifies_case_variables_for_ui(self) -> None:
        process = VALID_PROCESS + "| `legacy_unused` | string | Deprecated slot left from an older contract |\n"
        response = self.client.post(
            "/cases",
            json={
                "queue_message_id": "msg_observe_variables",
                "process_name": "demo",
                "process_path": "demo-process",
                "process_source": process,
                "title": "demo",
                "objective": "demo objective",
                "sender": "tester",
            },
        )
        self.assertEqual(response.status_code, 201)
        case_id = response.json()["case_id"]

        self.client.post(f"/cases/{case_id}/slots", json={"name": "request_id", "value": "req_123"})
        board = self.client.get(f"/cases/{case_id}/board").json()
        variables = {item["name"]: item for item in board["variables"]}

        self.assertEqual(variables["request_id"]["category"], "dispatcher_input")
        self.assertEqual(variables["request_id"]["status"], "filled")
        self.assertTrue(variables["request_id"]["has_value"])
        self.assertEqual(variables["request_id"]["value_preview"], '"req_123"')
        self.assertNotIn("value", variables["request_id"])
        self.assertEqual(variables["source_text"]["category"], "pending_output")
        self.assertEqual(variables["source_text"]["producer_step_number"], 1)
        self.assertEqual(variables["source_text"]["consumer_step_numbers"], [2])
        self.assertEqual(variables["legacy_unused"]["category"], "deprecated_or_unreferenced")
        self.assertEqual(board["variable_counts"]["deprecated_or_unreferenced"], 1)


if __name__ == "__main__":
    unittest.main()
