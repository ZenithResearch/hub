from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from services.cases.contract import compile_process_contract
from services.frank.case_pipeline_runner import CasePipelineRunner
from services.frank.review_case_automaton import REVIEW_SCOPE_FULL


class _FakeResponse:
    def __init__(self, payload=None, *, status_code=200, content=b"", headers=None):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class _RunnerClient:
    def __init__(self):
        self.operations: list[tuple[str, str, dict | None]] = []
        self.case_run_id = "case_run_1"
        self.step_run_by_step_db_id: dict[str, str] = {}
        self.events: list[dict] = []
        self.artifacts: list[dict] = []
        self.spans: list[dict] = []
        self.case_detail = {
            "case": {"id": "case_review_1", "status": "OPEN"},
            "contract": {
                "steps": [
                    {
                        "step_id": "step_1",
                        "output_variables": ["review_id_short", "audio_asset_path", "events"],
                    },
                    {
                        "step_id": "step_2",
                        "output_variables": ["transcript", "audio_offset_ms", "words"],
                    },
                ]
            },
            "steps": [
                {"id": "step_db_1", "step_id": "step_1", "name": "Load review record", "idx": 0, "status": "READY"},
                {"id": "step_db_2", "step_id": "step_2", "name": "Transcribe audio", "idx": 1, "status": "PENDING"},
            ],
            "slots": [
                {"name": "review_id", "value": '"review_12345678"'},
                {"name": "audio_asset_id", "value": '"audio_1"'},
                {"name": "events_asset_id", "value": '"events_1"'},
                {"name": "review_id_short", "value": None},
                {"name": "audio_asset_path", "value": None},
                {"name": "events", "value": None},
                {"name": "transcript", "value": None},
                {"name": "audio_offset_ms", "value": None},
                {"name": "words", "value": None},
            ],
        }

    async def get(self, url, timeout=None):
        self.operations.append(("GET", url, None))
        if url.endswith("/cases/case_review_1"):
            return _FakeResponse(self.case_detail)
        if url.endswith("/v1/reviews/assets/audio_1"):
            return _FakeResponse(content=b"fake-audio", headers={"content-type": "audio/webm"})
        if url.endswith("/v1/reviews/assets/events_1"):
            return _FakeResponse(content=b'[{"selector": ".button"}]', headers={"content-type": "application/json"})
        return _FakeResponse({}, status_code=404)

    async def post(self, url, json=None, timeout=None):
        self.operations.append(("POST", url, json))
        if url.endswith("/cases/case_review_1/runs"):
            return _FakeResponse({"id": self.case_run_id, "status": "running", "reused": False})
        if url.endswith(f"/case-runs/{self.case_run_id}/steps"):
            step_run_id = f"step_run_{json['step_db_row_id']}"
            self.step_run_by_step_db_id[json["step_db_row_id"]] = step_run_id
            return _FakeResponse({"id": step_run_id, **json})
        if url.endswith(f"/case-runs/{self.case_run_id}/events"):
            self.events.append(dict(json))
            return _FakeResponse({"id": f"event_{len(self.events)}", **json})
        if url.endswith(f"/case-runs/{self.case_run_id}/artifacts"):
            self.artifacts.append(dict(json))
            return _FakeResponse({"id": f"artifact_{len(self.artifacts)}", **json})
        if url.endswith(f"/case-runs/{self.case_run_id}/spans"):
            self.spans.append(dict(json))
            return _FakeResponse({"id": f"span_{len(self.spans)}", **json})
        if "/complete-outputs" in url:
            step_db_id = url.split("/steps/")[1].split("/")[0]
            for step in self.case_detail["steps"]:
                if step["id"] == step_db_id:
                    step["status"] = "COMPLETED"
            slot_values = {slot["name"]: slot for slot in self.case_detail["slots"]}
            for key, value in (json["outputs_json"] or {}).items():
                slot_values[key]["value"] = __import__("json").dumps(value)
            if step_db_id == "step_db_1":
                self.case_detail["steps"][1]["status"] = "READY"
            if step_db_id == "step_db_2":
                self.case_detail["case"]["status"] = "COMPLETED"
            return _FakeResponse({"ok": True})
        if url.endswith("/transcribe"):
            return _FakeResponse(
                {
                    "transcript": "hello world",
                    "words": [{"text": "hello", "start": 0.1, "end": 0.3}],
                    "language_code": "en",
                    "model": "tiny",
                }
            )
        if url.endswith("/logs"):
            return _FakeResponse({"log_id": "log_1"})
        return _FakeResponse({"ok": True})

    async def put(self, url, json=None, timeout=None):
        self.operations.append(("PUT", url, json))
        if url.endswith("/cases/case_review_1/status"):
            self.case_detail["case"]["status"] = json["status"]
            return _FakeResponse({"ok": True})
        if "/cases/case_review_1/steps/" in url:
            step_db_id = url.split("/steps/")[1].split("/")[0]
            for step in self.case_detail["steps"]:
                if step["id"] == step_db_id:
                    step["status"] = json["status"]
                    break
            return _FakeResponse({"ok": True})
        if "/execution-spans/" in url:
            return _FakeResponse({"ok": True})
        return _FakeResponse({"ok": True})

    async def patch(self, url, json=None, timeout=None):
        self.operations.append(("PATCH", url, json))
        return _FakeResponse({"review_id": "review_12345678", "status": (json or {}).get("status", "processed")})


class _SttDisconnectThenSuccessClient(_RunnerClient):
    def __init__(self, *, fail_attempts: int = 1):
        super().__init__()
        self.fail_attempts = fail_attempts
        self.transcribe_attempts = 0

    async def post(self, url, json=None, timeout=None):
        if url.endswith("/transcribe"):
            self.operations.append(("POST", url, json))
            self.transcribe_attempts += 1
            if self.transcribe_attempts <= self.fail_attempts:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return _FakeResponse(
                {
                    "transcript": "retry succeeded",
                    "words": [{"text": "retry", "start": 0.2, "end": 0.5}],
                    "language_code": "en",
                    "model": "tiny",
                }
            )
        return await super().post(url, json=json, timeout=timeout)


class FrankCasePipelineRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_step_2_retries_once_when_stt_disconnects(self) -> None:
        client = _SttDisconnectThenSuccessClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "audio.webm"
            audio_path.write_bytes(b"fake audio")
            case_dir = Path(tmpdir) / "case"
            case_dir.mkdir()
            (case_dir / "artifacts").mkdir()
            runner = CasePipelineRunner(
                client=client,
                cases_url="http://cases:8083",
                gateway_url="http://gateway-http:8080",
                stt_url="http://stt-http:8765",
                execution_root=Path(tmpdir),
            )
            case_detail = {
                **client.case_detail,
                "slots": [{"name": "audio_asset_path", "value": json.dumps(str(audio_path))}],
            }

            result = await runner.execute_step_2("case_run_1", "step_run_2", case_detail, case_dir)

        self.assertEqual(result["transcript"], "retry succeeded")
        self.assertEqual(client.transcribe_attempts, 2)
        transcribe_posts = [op for op in client.operations if op[0] == "POST" and op[1].endswith("/transcribe")]
        self.assertEqual(len(transcribe_posts), 2)

    async def test_step_2_waits_and_retries_multiple_transient_stt_disconnects(self) -> None:
        client = _SttDisconnectThenSuccessClient(fail_attempts=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "audio.webm"
            audio_path.write_bytes(b"fake audio")
            case_dir = Path(tmpdir) / "case"
            case_dir.mkdir()
            (case_dir / "artifacts").mkdir()
            runner = CasePipelineRunner(
                client=client,
                cases_url="http://cases:8083",
                gateway_url="http://gateway-http:8080",
                stt_url="http://stt-http:8765",
                execution_root=Path(tmpdir),
            )
            case_detail = {
                **client.case_detail,
                "slots": [{"name": "audio_asset_path", "value": json.dumps(str(audio_path))}],
            }

            with patch("services.frank.case_pipeline_runner.asyncio.sleep", new_callable=AsyncMock) as sleep:
                result = await runner.execute_step_2("case_run_1", "step_run_2", case_detail, case_dir)

        self.assertEqual(result["transcript"], "retry succeeded")
        self.assertEqual(client.transcribe_attempts, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_native_runner_executes_step_1_and_step_2_with_observability(self) -> None:
        client = _RunnerClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = CasePipelineRunner(
                client=client,
                cases_url="http://cases:8083",
                gateway_url="http://gateway-http:8080",
                stt_url="http://stt-http:8765",
                execution_root=Path(tmpdir),
            )
            result = await runner.run(
                "case_review_1",
                {
                    "event_type": "review_submitted",
                    "initial_context": {
                        "review_id": "review_12345678",
                        "audio_asset_id": "audio_1",
                        "events_asset_id": "events_1",
                    },
                },
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.completed_step_ids, ("step_1", "step_2"))
        self.assertTrue(any(event["type"] == "tool.call.started" for event in client.events))
        self.assertTrue(any(span["name"] == "stt-http transcription" for span in client.spans))
        self.assertTrue(any(artifact["role"] == "audio_asset" for artifact in client.artifacts))
        self.assertTrue(any(artifact["role"] == "transcript" for artifact in client.artifacts))

        step1_complete_index = next(
            index for index, (_, url, _) in enumerate(client.operations) if "/steps/step_db_1/complete-outputs" in url
        )
        step1_run_complete_index = next(
            index for index, (_, url, payload) in enumerate(client.operations) if url.endswith("/step-runs/step_run_step_db_1") and payload and payload.get("status") == "completed"
        )
        self.assertLess(step1_complete_index, step1_run_complete_index)

    async def test_native_runner_resumes_blocked_case_when_steps_are_runnable(self) -> None:
        client = _RunnerClient()
        client.case_detail["case"]["status"] = "BLOCKED"
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = CasePipelineRunner(
                client=client,
                cases_url="http://cases:8083",
                gateway_url="http://gateway-http:8080",
                stt_url="http://stt-http:8765",
                execution_root=Path(tmpdir),
            )
            result = await runner.run(
                "case_review_1",
                {
                    "event_type": "review_submitted",
                    "initial_context": {
                        "review_id": "review_12345678",
                        "audio_asset_id": "audio_1",
                        "events_asset_id": "events_1",
                    },
                },
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.completed_step_ids, ("step_1", "step_2"))
        in_progress_updates = [
            payload
            for method, url, payload in client.operations
            if method == "PUT" and url.endswith("/cases/case_review_1/status") and payload and payload.get("status") == "IN_PROGRESS"
        ]
        self.assertGreaterEqual(len(in_progress_updates), 1)

    async def test_create_case_run_reuses_idempotent_run_after_read_timeout(self) -> None:
        class TimeoutThenExistingRunClient(_RunnerClient):
            async def post(self, url, json=None, timeout=None):
                if url.endswith("/cases/case_review_1/runs"):
                    self.operations.append(("POST", url, json))
                    raise httpx.ReadTimeout("timed out creating case run")
                return await super().post(url, json=json, timeout=timeout)

            async def get(self, url, timeout=None):
                if url.endswith("/cases/case_review_1/runs"):
                    self.operations.append(("GET", url, None))
                    return _FakeResponse(
                        {
                            "case_runs": [
                                {
                                    "id": self.case_run_id,
                                    "status": "running",
                                    "idempotency_key": "native_case_pipeline:case_review_1",
                                    "reused_after_timeout": False,
                                }
                            ]
                        }
                    )
                return await super().get(url, timeout=timeout)

        client = TimeoutThenExistingRunClient()
        runner = CasePipelineRunner(
            client=client,
            cases_url="http://cases:8083",
            gateway_url="http://gateway-http:8080",
            stt_url="http://stt-http:8765",
            execution_root=Path("/tmp"),
        )

        case_run = await runner.create_case_run("case_review_1", {"event_type": "review_submitted"})

        self.assertEqual(case_run["id"], client.case_run_id)
        self.assertTrue(case_run["reused_after_timeout"])
        self.assertEqual(
            [(method, url) for method, url, _ in client.operations],
            [
                ("POST", "http://cases:8083/cases/case_review_1/runs"),
                ("GET", "http://cases:8083/cases/case_review_1/runs"),
            ],
        )

    async def test_create_case_run_retries_once_after_timeout_when_no_run_exists(self) -> None:
        class TimeoutThenRetryClient(_RunnerClient):
            def __init__(self):
                super().__init__()
                self.run_post_count = 0

            async def post(self, url, json=None, timeout=None):
                if url.endswith("/cases/case_review_1/runs"):
                    self.run_post_count += 1
                    self.operations.append(("POST", url, json))
                    if self.run_post_count == 1:
                        raise httpx.ReadTimeout("timed out creating case run")
                    return _FakeResponse({"id": self.case_run_id, "status": "running", "reused": False})
                return await super().post(url, json=json, timeout=timeout)

            async def get(self, url, timeout=None):
                if url.endswith("/cases/case_review_1/runs"):
                    self.operations.append(("GET", url, None))
                    return _FakeResponse({"case_runs": []})
                return await super().get(url, timeout=timeout)

        client = TimeoutThenRetryClient()
        runner = CasePipelineRunner(
            client=client,
            cases_url="http://cases:8083",
            gateway_url="http://gateway-http:8080",
            stt_url="http://stt-http:8765",
            execution_root=Path("/tmp"),
        )

        case_run = await runner.create_case_run("case_review_1", {"event_type": "review_submitted"})

        self.assertEqual(case_run["id"], client.case_run_id)
        self.assertEqual(client.run_post_count, 2)
        self.assertEqual(
            [(method, url) for method, url, _ in client.operations],
            [
                ("POST", "http://cases:8083/cases/case_review_1/runs"),
                ("GET", "http://cases:8083/cases/case_review_1/runs"),
                ("POST", "http://cases:8083/cases/case_review_1/runs"),
            ],
        )

    async def test_step_3_resolves_component_names_from_target_field(self) -> None:
        runner = CasePipelineRunner(
            client=_RunnerClient(),
            cases_url="http://cases:8083",
            gateway_url="http://gateway-http:8080",
            stt_url="http://stt-http:8765",
            execution_root=Path("/tmp"),
        )
        case_detail = {
            "slots": [
                {
                    "name": "events",
                    "value": json.dumps(
                        [
                            {
                                "id": 1,
                                "type": "click",
                                "target": "button.zh-notification-card__dismiss",
                                "elapsedMs": 100,
                                "x": 73,
                                "y": 51,
                            }
                        ]
                    ),
                }
            ]
        }

        outputs = await runner.execute_step_3(case_detail)

        self.assertEqual(outputs["component_names"][0]["component"], "button.zh-notification-card__dismiss")
        self.assertEqual(outputs["component_names"][0]["source"], "event.target")
        self.assertEqual(outputs["component_names"][0]["spatial_hint"], {"x": 73, "y": 51})

    async def test_step_5_extracts_nonempty_observations_from_transcript_and_events(self) -> None:
        runner = CasePipelineRunner(
            client=_RunnerClient(),
            cases_url="http://cases:8083",
            gateway_url="http://gateway-http:8080",
            stt_url="http://stt-http:8765",
            execution_root=Path("/tmp"),
        )
        case_detail = {
            "slots": [
                {"name": "review_id", "value": json.dumps("fd6269ef-cc22")},
                {"name": "review_id_short", "value": json.dumps("fd6269ef")},
                {"name": "transcript", "value": json.dumps("This X is not centered. When this tray is full this needs to show a number.")},
                {
                    "name": "words",
                    "value": json.dumps(
                        [
                            {"text": "This", "start_ms": 0, "end_ms": 100},
                            {"text": "X", "start_ms": 100, "end_ms": 200},
                            {"text": "is", "start_ms": 200, "end_ms": 300},
                            {"text": "not", "start_ms": 300, "end_ms": 400},
                            {"text": "centered.", "start_ms": 400, "end_ms": 500},
                            {"text": "When", "start_ms": 1800, "end_ms": 1900},
                            {"text": "this", "start_ms": 1900, "end_ms": 2000},
                            {"text": "tray", "start_ms": 2000, "end_ms": 2100},
                            {"text": "is", "start_ms": 2100, "end_ms": 2200},
                            {"text": "full", "start_ms": 2200, "end_ms": 2300},
                            {"text": "this", "start_ms": 2300, "end_ms": 2400},
                            {"text": "needs", "start_ms": 2400, "end_ms": 2500},
                            {"text": "to", "start_ms": 2500, "end_ms": 2600},
                            {"text": "show", "start_ms": 2600, "end_ms": 2700},
                            {"text": "a", "start_ms": 2700, "end_ms": 2800},
                            {"text": "number.", "start_ms": 2800, "end_ms": 2900},
                        ]
                    ),
                },
                {
                    "name": "events",
                    "value": json.dumps(
                        [
                            {"id": 1, "type": "click", "target": "button.zh-notification-card__dismiss", "elapsedMs": 200, "x": 73, "y": 51},
                            {"id": 2, "type": "click", "target": "span.zh-notification-dot", "elapsedMs": 2400, "x": 44, "y": 15},
                        ]
                    ),
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = await runner.execute_structured_analysis_baseline("step_5", case_detail, Path(tmpdir))
            packet_path = Path(tmpdir) / "artifacts" / "review_packet.json"

            self.assertTrue(packet_path.exists())
            self.assertGreaterEqual(len(outputs["observations"]), 2)
            self.assertEqual(outputs["observations"][0]["target_refs"], ["button.zh-notification-card__dismiss"])

    async def test_step_7_renders_review_document_from_feedback_items(self) -> None:
        runner = CasePipelineRunner(
            client=_RunnerClient(),
            cases_url="http://cases:8083",
            gateway_url="http://gateway-http:8080",
            stt_url="http://stt-http:8765",
            execution_root=Path("/tmp"),
        )
        case_detail = {
            "slots": [
                {"name": "review_id", "value": json.dumps("fd6269ef-cc22")},
                {"name": "review_id_short", "value": json.dumps("fd6269ef")},
                {"name": "transcript", "value": json.dumps("This X is not centered.")},
                {"name": "words", "value": json.dumps([{"text": "This", "start_ms": 0, "end_ms": 100}, {"text": "X", "start_ms": 100, "end_ms": 200}, {"text": "is", "start_ms": 200, "end_ms": 300}, {"text": "not", "start_ms": 300, "end_ms": 400}, {"text": "centered.", "start_ms": 400, "end_ms": 500}])},
                {"name": "events", "value": json.dumps([{"id": 1, "type": "click", "target": "button.zh-notification-card__dismiss", "elapsedMs": 200, "x": 73, "y": 51}])},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = await runner.execute_structured_analysis_baseline("step_7", case_detail, Path(tmpdir))
            review_path = Path(outputs["review_note_path"])

            content = review_path.read_text(encoding="utf-8")
            self.assertIn("## Feedback Items", content)
            self.assertIn("dismiss/X control", content)
            self.assertNotIn("Issues identified\n\n[]", content)

    async def test_step_8_records_review_packet_status(self) -> None:
        client = _RunnerClient()
        runner = CasePipelineRunner(
            client=client,
            cases_url="http://cases:8083",
            gateway_url="http://gateway-http:8080",
            stt_url="http://stt-http:8765",
            execution_root=Path("/tmp"),
        )
        case_detail = {
            "slots": [
                {"name": "review_id", "value": json.dumps("review_12345678")},
                {"name": "review_note_path", "value": json.dumps("/tmp/review.md")},
                {"name": "transcript", "value": json.dumps("This X is not centered.")},
                {"name": "observations", "value": json.dumps([{"id": "fb_001"}])},
                {"name": "component_names", "value": json.dumps([{"component": "button.x"}])},
            ]
        }

        outputs = await runner.execute_step_8(case_detail)

        patch_payload = next(payload for method, url, payload in client.operations if method == "PATCH" and url.endswith("/v1/reviews/review_12345678/status"))
        self.assertEqual(patch_payload["status"], "processed")
        self.assertEqual(patch_payload["automaton_status"], "succeeded")
        self.assertEqual(patch_payload["automaton_event"], "review_passed")
        self.assertEqual(patch_payload["reason"], "review_passed")
        self.assertEqual(patch_payload["review_scope"], REVIEW_SCOPE_FULL)
        self.assertEqual(patch_payload["review_packet_status"], "needs_human_review")
        self.assertEqual(outputs["review_status_updated"]["status"], "processed")
        self.assertEqual(outputs["review_status_updated"]["automaton_status"], "succeeded")
        self.assertEqual(outputs["review_status_updated"]["automaton_event"], "review_passed")
        self.assertEqual(outputs["review_status_updated"]["reason"], "review_passed")
        self.assertEqual(outputs["review_status_updated"]["review_scope"], REVIEW_SCOPE_FULL)
        self.assertEqual(outputs["review_status_updated"]["review_packet_status"], "needs_human_review")

    async def test_step_8_degraded_packet_completes_case_step_after_status_writeback(self) -> None:
        client = _RunnerClient()
        runner = CasePipelineRunner(
            client=client,
            cases_url="http://cases:8083",
            gateway_url="http://gateway-http:8080",
            stt_url="http://stt-http:8765",
            execution_root=Path("/tmp"),
        )
        case_detail = {
            "case": {"id": "case_review_1", "status": "OPEN"},
            "slots": [
                {"name": "review_id", "value": json.dumps("review_12345678")},
                {"name": "review_note_path", "value": json.dumps("/tmp/review.md")},
                {"name": "transcript", "value": json.dumps("This X is not centered.")},
                {"name": "observations", "value": json.dumps([{"id": "fb_001"}])},
                {"name": "component_names", "value": json.dumps([{"component": "button.x"}])},
                {"name": "review_status_updated", "value": None},
            ],
        }
        step = {"id": "step_db_8", "step_id": "step_8", "name": "Update review status", "idx": 7, "status": "READY"}
        client.case_detail = {
            **case_detail,
            "steps": [step],
            "contract": {"steps": [{"step_id": "step_8", "output_variables": ["review_status_updated"]}]},
        }

        await runner.run_step("case_run_1", "case_review_1", {}, case_detail, step, Path("/tmp"))

        patch_payload = next(payload for method, url, payload in client.operations if method == "PATCH" and url.endswith("/v1/reviews/review_12345678/status"))
        self.assertEqual(patch_payload["status"], "processed")
        self.assertEqual(patch_payload["automaton_status"], "succeeded")
        self.assertEqual(patch_payload["automaton_event"], "review_passed")
        self.assertEqual(patch_payload["reason"], "review_passed")
        self.assertEqual(patch_payload["review_packet_status"], "needs_human_review")
        complete_output_index = next(index for index, (_, url, _) in enumerate(client.operations) if "/steps/step_db_8/complete-outputs" in url)
        step_run_index, step_run_update = next((index, payload) for index, (method, url, payload) in enumerate(client.operations) if method == "PUT" and url.endswith("/step-runs/step_run_step_db_8"))
        self.assertLess(complete_output_index, step_run_index)
        self.assertEqual(step_run_update["status"], "completed")
        self.assertFalse(any(method == "PUT" and url.endswith("/cases/case_review_1/steps/step_db_8") for method, url, _ in client.operations))
        self.assertFalse(any(event["type"] == "step.failed" for event in client.events))

    async def test_step_8_records_transcript_only_status_reason(self) -> None:
        client = _RunnerClient()
        runner = CasePipelineRunner(
            client=client,
            cases_url="http://cases:8083",
            gateway_url="http://gateway-http:8080",
            stt_url="http://stt-http:8765",
            execution_root=Path("/tmp"),
        )
        case_detail = {
            "slots": [
                {"name": "review_id", "value": json.dumps("review_12345678")},
                {"name": "review_note_path", "value": json.dumps("/tmp/review.md")},
                {"name": "transcript", "value": json.dumps("Just looking around.")},
                {"name": "observations", "value": json.dumps([])},
                {"name": "component_names", "value": json.dumps([])},
            ]
        }

        outputs = await runner.execute_step_8(case_detail)

        patch_payload = next(payload for method, url, payload in client.operations if method == "PATCH" and url.endswith("/v1/reviews/review_12345678/status"))
        self.assertEqual(patch_payload["status"], "processed")
        self.assertEqual(patch_payload["automaton_status"], "succeeded")
        self.assertEqual(patch_payload["automaton_event"], "review_passed")
        self.assertEqual(patch_payload["reason"], "review_passed")
        self.assertEqual(outputs["review_status_updated"]["review_packet_status"], "transcript_only")

    async def test_step_8_ready_packet_records_successful_automaton_metadata(self) -> None:
        client = _RunnerClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "case_review_1" / "artifacts"
            packet_dir.mkdir(parents=True)
            (packet_dir / "review_packet.json").write_text(
                json.dumps({"quality": {"status": "review_packet_ready"}}),
                encoding="utf-8",
            )
            runner = CasePipelineRunner(client=client, cases_url="http://cases:8083", gateway_url="http://gateway-http:8080", stt_url="http://stt-http:8765", execution_root=Path(tmpdir))
            case_detail = {
                "case": {"id": "case_review_1"},
                "slots": [
                    {"name": "review_id", "value": json.dumps("review_12345678")},
                    {"name": "review_note_path", "value": json.dumps("/tmp/review.md")},
                    {"name": "transcript", "value": json.dumps("This X is not centered.")},
                    {"name": "observations", "value": json.dumps([{"id": "fb_001"}])},
                    {"name": "component_names", "value": json.dumps([{"component": "button.x"}])},
                ],
            }

            outputs = await runner.execute_step_8(case_detail)

        patch_payload = next(payload for method, url, payload in client.operations if method == "PATCH" and url.endswith("/v1/reviews/review_12345678/status"))
        self.assertEqual(patch_payload["status"], "processed")
        self.assertEqual(patch_payload["automaton_status"], "succeeded")
        self.assertEqual(patch_payload["automaton_event"], "review_passed")
        self.assertEqual(patch_payload["reason"], "review_passed")
        self.assertEqual(patch_payload["review_scope"], REVIEW_SCOPE_FULL)
        self.assertEqual(outputs["review_status_updated"]["status"], "processed")
        self.assertEqual(outputs["review_status_updated"]["automaton_status"], "succeeded")
        self.assertEqual(outputs["review_status_updated"]["review_scope"], REVIEW_SCOPE_FULL)


    async def test_step_5_outputs_packet_status_path_and_target_events(self) -> None:
        runner = CasePipelineRunner(client=_RunnerClient(), cases_url="http://cases:8083", gateway_url="http://gateway-http:8080", stt_url="http://stt-http:8765", execution_root=Path("/tmp"))
        case_detail = {
            "slots": [
                {"name": "review_id", "value": json.dumps("fd6269ef-cc22")},
                {"name": "review_id_short", "value": json.dumps("fd6269ef")},
                {"name": "transcript", "value": json.dumps("This X is not centered.")},
                {"name": "words", "value": json.dumps([{"text": "This", "start_ms": 0, "end_ms": 100}, {"text": "X", "start_ms": 100, "end_ms": 200}, {"text": "is", "start_ms": 200, "end_ms": 300}, {"text": "not", "start_ms": 300, "end_ms": 400}, {"text": "centered.", "start_ms": 400, "end_ms": 500}])},
                {"name": "events", "value": json.dumps([{"id": 1, "type": "click", "target": "button.zh-notification-card__dismiss", "elapsedMs": 200, "x": 73, "y": 51}])},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = await runner.execute_structured_analysis_baseline("step_5", case_detail, Path(tmpdir))

        self.assertTrue(outputs["review_packet_path"].endswith("review_packet.json"))
        self.assertEqual(outputs["review_packet_status"], "needs_source_binding")
        self.assertEqual(outputs["target_events"][0]["target_ref"], "button.zh-notification-card__dismiss")
        self.assertGreaterEqual(len(outputs["observations"]), 1)
        self.assertIn("actionable_now", outputs["actionability"])
        self.assertIn("silent_annotations", outputs["negative_evidence"])
        self.assertIn("implementation_tasks", outputs["implementation_handoff"])
        self.assertEqual(
            set(outputs),
            {
                "observations",
                "target_events",
                "review_packet_path",
                "review_packet_status",
                "actionability",
                "negative_evidence",
                "implementation_handoff",
                "silent_annotations",
                "filtered_points",
            },
        )

    async def test_structured_analysis_outputs_match_current_process_contract(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "base/ops/processes/process-queued-review.md").read_text(encoding="utf-8")
        contract = compile_process_contract(source, process_path="base/ops/processes/process-queued-review")
        expected_by_step = {step["step_id"]: set(step["output_variables"]) for step in contract["steps"]}
        runner = CasePipelineRunner(client=_RunnerClient(), cases_url="http://cases:8083", gateway_url="http://gateway-http:8080", stt_url="http://stt-http:8765", execution_root=Path("/tmp"))
        base_case_detail = {
            "slots": [
                {"name": "review_id", "value": json.dumps("fd6269ef-cc22")},
                {"name": "review_id_short", "value": json.dumps("fd6269ef")},
                {"name": "subject_id", "value": json.dumps("http://unmapped.example/?reviewMode=on")},
                {"name": "submitted_by", "value": json.dumps("tester")},
                {"name": "reviewed_at", "value": json.dumps("2026-05-06T00:00:00Z")},
                {"name": "duration_ms", "value": json.dumps(1000)},
                {"name": "transcript", "value": json.dumps("This X is not centered.")},
                {"name": "resolved_transcript", "value": json.dumps("This X is not centered.")},
                {"name": "words", "value": json.dumps([{"text": "This", "start_ms": 0, "end_ms": 100}, {"text": "X", "start_ms": 100, "end_ms": 200}, {"text": "is", "start_ms": 200, "end_ms": 300}, {"text": "not", "start_ms": 300, "end_ms": 400}, {"text": "centered.", "start_ms": 400, "end_ms": 500}])},
                {"name": "events", "value": json.dumps([{"id": 1, "type": "click", "target": "button.zh-notification-card__dismiss", "elapsedMs": 200, "x": 73, "y": 51}])},
                {"name": "observations", "value": json.dumps([{"id": "fb_001", "target_refs": ["button.zh-notification-card__dismiss"]}])},
                {"name": "component_names", "value": json.dumps([{"component": "button.zh-notification-card__dismiss", "selectors": ["button.zh-notification-card__dismiss"]}])},
                {"name": "silent_annotations", "value": json.dumps([])},
                {"name": "filtered_points", "value": json.dumps([])},
                {"name": "codebase_context", "value": json.dumps([{"feedback_item_id": "fb_001", "status": "deferred", "reason": "source binding unavailable", "target_refs": ["button.zh-notification-card__dismiss"], "selectors": ["button.zh-notification-card__dismiss"], "references": [], "files_to_inspect_first": [], "open_questions": ["Which repo?"]}])},
                {"name": "review_packet_path", "value": json.dumps("/tmp/review_packet.json")},
                {"name": "review_packet_status", "value": json.dumps("needs_source_binding")},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            case_dir = Path(tmpdir)
            (case_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            for step_id in ["step_4", "step_5", "step_6", "step_7"]:
                outputs = await runner.execute_structured_analysis_baseline(step_id, base_case_detail, case_dir)
                self.assertEqual(set(outputs), expected_by_step[step_id], step_id)

    async def test_step_6_returns_per_feedback_deferred_source_bindings(self) -> None:
        runner = CasePipelineRunner(client=_RunnerClient(), cases_url="http://cases:8083", gateway_url="http://gateway-http:8080", stt_url="http://stt-http:8765", execution_root=Path("/tmp"))
        case_detail = {
            "slots": [
                {"name": "subject_id", "value": json.dumps("http://unmapped.example/?reviewMode=on")},
                {"name": "observations", "value": json.dumps([{"id": "fb_001", "target_refs": ["button.x"]}, {"id": "fb_002", "target_refs": ["span.badge"]}])},
                {"name": "component_names", "value": json.dumps([{"component": "button.x", "selectors": ["button.x"]}, {"component": "span.badge", "selectors": ["span.badge"]}])},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = await runner.execute_structured_analysis_baseline("step_6", case_detail, Path(tmpdir))

        bindings = outputs["codebase_context"]
        self.assertEqual(set(outputs), {"codebase_context"})
        self.assertEqual([binding["feedback_item_id"] for binding in bindings], ["fb_001", "fb_002"])
        self.assertTrue(all(binding["status"] == "deferred" for binding in bindings))
        self.assertTrue(all(binding["reason"] for binding in bindings))
        self.assertTrue(all(binding["open_questions"] for binding in bindings))

    async def test_step_6_returns_verified_source_bindings_when_subject_codebase_is_mapped(self) -> None:
        runner = CasePipelineRunner(client=_RunnerClient(), cases_url="http://cases:8083", gateway_url="http://gateway-http:8080", stt_url="http://stt-http:8765", execution_root=Path("/tmp"))
        with tempfile.TemporaryDirectory() as codebase_root, patch.dict("os.environ", {"FRANK_SUBJECT_CODEBASE_MAP": f"http://localhost:3000={codebase_root}"}, clear=False):
            source_file = Path(codebase_root) / "src/components/hub/LandingPage.tsx"
            source_file.parent.mkdir(parents=True)
            source_file.write_text('<button className="zh-notification-card__dismiss" />\n', encoding="utf-8")
            case_detail = {
                "slots": [
                    {"name": "subject_id", "value": json.dumps("http://localhost:3000/?reviewMode=on")},
                    {"name": "observations", "value": json.dumps([{"id": "fb_001", "target_refs": ["button.zh-notification-card__dismiss"]}])},
                    {"name": "component_names", "value": json.dumps([{"component": "button.zh-notification-card__dismiss", "selectors": ["button.zh-notification-card__dismiss"]}])},
                ]
            }
            outputs = await runner.execute_structured_analysis_baseline("step_6", case_detail, Path(codebase_root))

        bindings = outputs["codebase_context"]
        self.assertEqual(bindings[0]["status"], "verified")
        self.assertEqual(bindings[0]["references"][0]["relative_path"], "src/components/hub/LandingPage.tsx")
        self.assertEqual(bindings[0]["files_to_inspect_first"], [bindings[0]["references"][0]["path"]])
        self.assertEqual(bindings[0]["open_questions"], [])

    async def test_step_7_markdown_surfaces_deferred_binding_warning_and_handoff(self) -> None:
        runner = CasePipelineRunner(client=_RunnerClient(), cases_url="http://cases:8083", gateway_url="http://gateway-http:8080", stt_url="http://stt-http:8765", execution_root=Path("/tmp"))
        case_detail = {
            "slots": [
                {"name": "review_id", "value": json.dumps("fd6269ef-cc22")},
                {"name": "review_id_short", "value": json.dumps("fd6269ef")},
                {"name": "transcript", "value": json.dumps("This X is not centered.")},
                {"name": "words", "value": json.dumps([{"text": "This", "start_ms": 0, "end_ms": 100}, {"text": "X", "start_ms": 100, "end_ms": 200}, {"text": "is", "start_ms": 200, "end_ms": 300}, {"text": "not", "start_ms": 300, "end_ms": 400}, {"text": "centered.", "start_ms": 400, "end_ms": 500}])},
                {"name": "events", "value": json.dumps([{"id": 1, "type": "click", "target": "button.zh-notification-card__dismiss", "elapsedMs": 200}])},
                {"name": "codebase_context", "value": json.dumps([{"feedback_item_id": "fb_001", "status": "deferred", "reason": "source binding unavailable", "target_refs": ["button.zh-notification-card__dismiss"], "selectors": ["button.zh-notification-card__dismiss"], "references": [], "files_to_inspect_first": [], "open_questions": ["Which repo?"]}])},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs = await runner.execute_structured_analysis_baseline("step_7", case_detail, Path(tmpdir))
            self.assertEqual(set(outputs), {"review_note_path"})
            content = Path(outputs["review_note_path"]).read_text(encoding="utf-8")

        self.assertIn("## Implementation Handoff", content)
        self.assertIn("degraded", content)
        self.assertIn("Source binding: `deferred`", content)
        self.assertIn("Do not create ISS notes", content)
        self.assertIn("## Non-goals", content)

    async def test_step_8_unreadable_packet_degrades_status_visibly(self) -> None:
        client = _RunnerClient()
        with tempfile.TemporaryDirectory() as tmpdir:
            packet_dir = Path(tmpdir) / "case_review_1" / "artifacts"
            packet_dir.mkdir(parents=True)
            (packet_dir / "review_packet.json").write_text("{not-json", encoding="utf-8")
            runner = CasePipelineRunner(client=client, cases_url="http://cases:8083", gateway_url="http://gateway-http:8080", stt_url="http://stt-http:8765", execution_root=Path(tmpdir))
            case_detail = {
                "case": {"id": "case_review_1"},
                "slots": [
                    {"name": "review_id", "value": json.dumps("review_12345678")},
                    {"name": "review_note_path", "value": json.dumps("/tmp/review.md")},
                    {"name": "transcript", "value": json.dumps("Just looking around.")},
                    {"name": "observations", "value": json.dumps([])},
                    {"name": "component_names", "value": json.dumps([])},
                ],
            }
            outputs = await runner.execute_step_8(case_detail)

        patch_payload = next(payload for method, url, payload in client.operations if method == "PATCH" and url.endswith("/v1/reviews/review_12345678/status"))
        self.assertEqual(patch_payload["status"], "processed")
        self.assertEqual(patch_payload["automaton_status"], "succeeded")
        self.assertEqual(patch_payload["automaton_event"], "review_passed")
        self.assertEqual(patch_payload["reason"], "review_passed")
        self.assertEqual(outputs["review_status_updated"]["review_packet_path"].endswith("review_packet.json"), True)


if __name__ == "__main__":
    unittest.main()
