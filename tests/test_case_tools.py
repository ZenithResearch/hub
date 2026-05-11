from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from libs.tools.cases import tool as case_tools
from libs.tools.registry import ToolRegistry
from libs.tools.sandbox_runner import sanitized_env


class CaseToolTests(unittest.TestCase):
    def test_sanitized_env_forwards_manifest_env_vars(self) -> None:
        with patch.dict(os.environ, {"CASES_HTTP_URL": "http://cases:8083"}, clear=False):
            env = sanitized_env(
                request_id="req_123",
                tool_name="write_slot",
                env_vars=["CASES_HTTP_URL"],
            )
        self.assertEqual(env["CASES_HTTP_URL"], "http://cases:8083")
        self.assertEqual(env["REQUEST_ID"], "req_123")

    def test_sanitized_env_loads_allowlisted_config_secret_file(self) -> None:
        secret_path = Path(self.id().replace('.', '_'))
        try:
            secret_path.write_text("ELEVENLABS_API_KEY=test_file_value\nOPENROUTER_API_KEY=should_not_forward\n")
            with patch.dict(os.environ, {"HUB_CONFIG_SECRETS_PATH": str(secret_path)}, clear=True):
                env = sanitized_env(
                    request_id="req_123",
                    tool_name="elevenlabs_stt",
                    env_vars=["ELEVENLABS_API_KEY"],
                )
            self.assertEqual(env["ELEVENLABS_API_KEY"], "test_file_value")
            self.assertNotIn("OPENROUTER_API_KEY", env)
        finally:
            secret_path.unlink(missing_ok=True)

    def test_registry_loads_case_tools_from_root_tool_dir(self) -> None:
        registry = ToolRegistry(tool_dir=str(Path(__file__).resolve().parents[1] / "libs/tools"))
        registry.load()
        self.assertIsNotNone(registry.get("get_case"))
        self.assertIsNotNone(registry.get("write_slot"))
        self.assertIsNotNone(registry.get("complete_step_outputs"))
        self.assertIsNotNone(registry.get("update_step_runtime_state"))
        self.assertIsNotNone(registry.get("update_review_status"))
        self.assertEqual(registry.get("write_slot").env_vars, ["CASES_HTTP_URL"])
        self.assertEqual(registry.get("update_review_status").env_vars, ["GATEWAY_HTTP_URL"])

    @patch("libs.tools.cases.tool._request")
    def test_set_step_running_calls_cases_api(self, request_mock) -> None:
        request_mock.return_value = {"ok": True}
        result = case_tools.set_step_running(
            {"case_id": "case_1", "step_db_row_id": "step_db_1"},
            request_id="req_123",
        )
        request_mock.assert_called_once_with(
            "PUT",
            "/cases/case_1/steps/step_db_1",
            json_body={"status": "RUNNING"},
        )
        self.assertEqual(result["step_db_row_id"], "step_db_1")

    @patch("libs.tools.cases.tool._request")
    def test_write_slot_forwards_agent_run_id(self, request_mock) -> None:
        request_mock.return_value = {"ok": True}
        result = case_tools.write_slot(
            {
                "case_id": "case_1",
                "name": "summary",
                "value": "brief",
                "agent_run_id": "run_123",
            },
            request_id="req_123",
        )
        request_mock.assert_called_once_with(
            "POST",
            "/cases/case_1/slots",
            json_body={"name": "summary", "value": "brief", "agent_run_id": "run_123"},
        )
        self.assertEqual(result["agent_run_id"], "run_123")

    @patch("libs.tools.cases.tool._request")
    def test_update_step_runtime_state_calls_cases_api(self, request_mock) -> None:
        request_mock.return_value = {"ok": True, "runtime_updated_at": "2026-04-30T20:00:00Z"}
        result = case_tools.update_step_runtime_state(
            {
                "case_id": "case_1",
                "step_db_row_id": "step_db_1",
                "runtime_state_json": {"status": "active", "task_count": 3},
            },
            request_id="req_123",
        )
        request_mock.assert_called_once_with(
            "PUT",
            "/cases/case_1/steps/step_db_1/runtime-state",
            json_body={"runtime_state_json": {"status": "active", "task_count": 3}},
        )
        self.assertEqual(result["step_db_row_id"], "step_db_1")

    @patch("libs.tools.cases.tool._request")
    def test_complete_step_outputs_calls_cases_api(self, request_mock) -> None:
        request_mock.return_value = {"ok": True, "completed_at": "2026-05-01T00:00:00Z"}
        result = case_tools.complete_step_outputs(
            {
                "case_id": "case_1",
                "step_db_row_id": "step_db_1",
                "outputs_json": {"summary": "brief"},
                "agent_run_id": "run_123",
                "notes": ["done"],
            },
            request_id="req_123",
        )
        request_mock.assert_called_once_with(
            "POST",
            "/cases/case_1/steps/step_db_1/complete-outputs",
            json_body={
                "outputs_json": {"summary": "brief"},
                "agent_run_id": "run_123",
                "notes": ["done"],
            },
        )
        self.assertEqual(result["step_db_row_id"], "step_db_1")

    def test_update_review_status_requires_gateway_http_url(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("libs.tools.cases.tool._request") as request_mock:
            with self.assertRaisesRegex(RuntimeError, "GATEWAY_HTTP_URL is required"):
                case_tools.update_review_status(
                    {"review_id": "review-123", "status": "processed"},
                    request_id="req_123",
                )
        request_mock.assert_not_called()

    @patch("libs.tools.cases.tool.httpx.patch")
    @patch("libs.tools.cases.tool._request")
    def test_update_review_status_calls_gateway_not_cases_api(self, request_mock, httpx_patch_mock) -> None:
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, str]:
                return {"review_id": "review-123", "status": "processed"}

        httpx_patch_mock.return_value = Response()
        with patch.dict(os.environ, {"GATEWAY_HTTP_URL": "http://gateway-http:8080/"}, clear=True):
            result = case_tools.update_review_status(
                {
                    "review_id": "review-123",
                    "status": "processed",
                    "review_note_path": "~/claude-hub/notes/review review-123.md",
                },
                request_id="req_123",
            )
        request_mock.assert_not_called()
        httpx_patch_mock.assert_called_once_with(
            "http://gateway-http:8080/v1/reviews/review-123/status",
            json={"status": "processed", "review_note_path": "~/claude-hub/notes/review review-123.md"},
            timeout=20.0,
        )
        self.assertEqual(result["review_id"], "review-123")
        self.assertEqual(result["status"], "processed")

    @patch("libs.tools.cases.tool.get_case_payload")
    @patch("libs.tools.cases.tool._request")
    def test_set_step_completed_rejects_output_producing_steps(self, request_mock, get_case_mock) -> None:
        get_case_mock.return_value = {
            "steps": [{"id": "step_db_1", "step_id": "step_1"}],
            "contract": {"steps": [{"step_id": "step_1", "output_variables": ["summary"]}]},
        }
        with self.assertRaisesRegex(ValueError, "declares outputs"):
            case_tools.set_step_completed(
                {"case_id": "case_1", "step_db_row_id": "step_db_1"},
                request_id="req_123",
            )
        request_mock.assert_not_called()

    @patch("libs.tools.cases.tool.get_case_payload")
    @patch("libs.tools.cases.tool._request")
    def test_set_step_completed_allows_no_output_steps(self, request_mock, get_case_mock) -> None:
        get_case_mock.return_value = {
            "steps": [{"id": "step_db_2", "step_id": "step_2"}],
            "contract": {"steps": [{"step_id": "step_2", "output_variables": []}]},
        }
        request_mock.return_value = {"ok": True}
        result = case_tools.set_step_completed(
            {"case_id": "case_1", "step_db_row_id": "step_db_2"},
            request_id="req_123",
        )
        request_mock.assert_called_once_with(
            "PUT",
            "/cases/case_1/steps/step_db_2",
            json_body={"status": "COMPLETED"},
        )
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
