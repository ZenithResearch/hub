from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx


class _FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, case_payload: dict, case_detail: dict):
        self.case_payload = case_payload
        self.case_detail = case_detail
        self.posts: list[tuple[str, dict | None, dict | None]] = []
        self.puts: list[tuple[str, dict | None]] = []
        self.gets: list[str] = []
        self.operations: list[tuple[str, str]] = []

    async def post(self, url: str, json: dict | None = None, params: dict | None = None, timeout: float | None = None):
        self.posts.append((url, json, params))
        self.operations.append(("POST", url))
        if url.endswith("/cases"):
            return _FakeResponse(self.case_payload, 201)
        if "/queues/workers/enqueue" in url:
            return _FakeResponse({"id": "msg_worker_1", "message_id": "msg_worker_1"}, 201)
        if url.endswith("/publish"):
            return _FakeResponse({"ok": True}, 202)
        return _FakeResponse({"ok": True, "log_id": "log_123"}, 201)

    async def get(self, url: str, params: dict | None = None, timeout: float | None = None):
        self.gets.append(url)
        self.operations.append(("GET", url))
        if "/cases/" in url:
            return _FakeResponse(self.case_detail, 200)
        return _FakeResponse({}, 404)

    async def put(self, url: str, json: dict | None = None, timeout: float | None = None):
        self.puts.append((url, json))
        self.operations.append(("PUT", url))
        return _FakeResponse({"ok": True}, 200)


class FrankDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        (root / "base/ops/processes").mkdir(parents=True)
        (root / "rolodex/agents/frank").mkdir(parents=True)
        (root / "rolodex/agents/frank/config.yaml").write_text("model:\n  provider: openai\n  model: gpt-5.3-codex\n", encoding="utf-8")
        (root / "base/ops/processes/process-queued-review.md").write_text(
            """---
title: "Process queued review"
doc_type: processes
tags: [review]
---

# Process queued review

## When to use

Use when a `review_submitted` event arrives.
Trigger: `event_type = review_submitted`, payload contains `audio_asset_id`, `events_asset_id`, and `review_id`.

## What this process does

Process a review.

## Steps

### Step 1 — Load review

**Executor:** `frank`

**Input:** `review_id`, `audio_asset_id`, `events_asset_id`

**Processing:** Load the review.

**Output (process state):**
```json
{
  "summary": "..."
}
```

## Variables

| Variable | Type | Description |
|---|---|---|
| `review_id` | string | Review id |
| `audio_asset_id` | string | Audio asset |
| `events_asset_id` | string | Events asset |
| `subject_id` | string | Subject |
| `submitted_by` | string | Submitter |
| `reviewed_at` | string | Review timestamp |
| `duration_ms` | number | Duration |
| `audio_asset_path` | string (path) | Audio path |
| `summary` | string | Step output |
"""
        )
        (root / "rolodex/index.yaml").write_text(
            """agents:
  entries:
    frank:
      path: agents/frank/
      hermes_home: .hermes/workers/frank
"""
        )

        os.environ["QUEUE_HTTP_URL"] = "http://queue:8081"
        os.environ["EVENTBUS_URL"] = "http://eventbus:8082"
        os.environ["CASES_HTTP_URL"] = "http://cases:8083"
        os.environ["TERMINAL_CWD"] = str(root)
        os.environ["WORKER_HOST"] = "host.docker.internal"
        os.environ["WORKER_CASES_URL"] = "http://localhost:8083"
        os.environ["PROCESS_HUBFS_ROOT"] = "/app/base/ops/processes"
        os.environ["MODEL"] = ""
        os.environ["FRANK_MODEL"] = ""
        os.environ["OPENAI_BASE_URL"] = ""
        os.environ["OPENAI_API_KEY"] = ""
        os.environ["OPENROUTER_API_KEY"] = ""
        os.environ.pop("FRANK_RUNTIME", None)

        sys.modules.pop("services.frank.main", None)
        sys.modules["yaml"] = importlib.import_module("yaml")
        module = importlib.import_module("services.frank.main")
        self.module = importlib.reload(module)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_missing_capability_env_vars_reads_config_secret_file(self) -> None:
        secret_path = Path(self.tmpdir.name) / "config-secrets.env"
        secret_path.write_text("ELEVENLABS_API_KEY=test_f...\n")
        with patch.dict(os.environ, {"HUB_CONFIG_SECRETS_PATH": str(secret_path)}, clear=True):
            missing = self.module.missing_capability_env_vars({"env_vars": ["ELEVENLABS_API_KEY"]})
        self.assertEqual(missing, [])

    def test_missing_capability_env_vars_reports_absent_stt_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            missing = self.module.missing_capability_env_vars({"env_vars": ["ELEVENLABS_API_KEY"]})
        self.assertEqual(missing, ["ELEVENLABS_API_KEY"])

    def test_process_definition_uses_gateway_hubfs_process_path(self) -> None:
        process_def = self.module.resolve_process_definition(
            {"id": "msg_1", "event_type": "review_submitted", "sender": "tester", "payload": {}}
        )

        self.assertEqual(process_def.process_name, "review_submitted")
        self.assertEqual(process_def.process_path, "/app/base/ops/processes/process-queued-review.md")
        self.assertTrue(process_def.path.exists())


    def test_build_step_briefs_propagates_parsed_toolsets_to_allowed_toolsets(self) -> None:
        contract = {
            "variables": {},
            "steps": [
                {
                    "step_id": "step_3",
                    "title": "Create review document",
                    "instructions": "Create the review document.",
                    "input_items": [],
                    "output_variables": [],
                    "tools": [],
                    "toolsets": ["browser"],
                    "resources": [],
                    "suggested_resources": [],
                    "skills": [],
                    "executor": "frank",
                    "assignee": "worker",
                }
            ],
        }
        case_steps = [
            {
                "id": "step_db_3",
                "step_id": "step_3",
                "name": "Create review document",
                "executor": "frank",
                "action": "Create review document",
            }
        ]

        step_briefs = self.module.build_step_briefs(contract, case_steps)

        self.assertEqual(step_briefs[0]["toolsets"], ["browser"])
        self.assertEqual(step_briefs[0]["executor"], "frank")
        self.assertEqual(step_briefs[0]["assignee"], "worker")

    def test_derive_workspace_policy_emits_only_hermes_valid_values(self) -> None:
        cases = [
            (["review assets workspace"], "case_123", "dir:/hub/.hermes/frank_execution/case_123/assets"),
            (["subject codebase"], None, "worktree"),
            (["hub repo"], None, "worktree"),
            (["vault", "daily note"], None, "scratch"),
            (["notes workspace"], None, "scratch"),
            ([], None, "scratch"),
        ]
        for resources, case_id, expected in cases:
            with self.subTest(resources=resources, case_id=case_id):
                policy = self.module.derive_workspace_policy(resources, case_id=case_id)
                self.assertEqual(policy, expected)
                self.assertFalse(policy.startswith("worktree:"))
                self.assertFalse(policy.startswith("scratch:"))

    async def test_dispatch_workspace_policies_do_not_use_invalid_hermes_prefixes(self) -> None:
        contract = {
            "variables": {},
            "steps": [
                {
                    "step_id": "step_1",
                    "title": "Use codebase",
                    "instructions": "Inspect the codebase.",
                    "input_items": [],
                    "output_variables": [],
                    "tools": [],
                    "toolsets": [],
                    "resources": ["subject codebase"],
                    "suggested_resources": [],
                    "skills": [],
                },
                {
                    "step_id": "step_2",
                    "title": "Log note",
                    "instructions": "Write a note.",
                    "input_items": [],
                    "output_variables": [],
                    "tools": [],
                    "toolsets": [],
                    "resources": ["daily note"],
                    "suggested_resources": [],
                    "skills": [],
                },
            ],
        }
        case_steps = [
            {"id": "step_db_1", "step_id": "step_1", "name": "Use codebase", "executor": "frank", "action": "Use codebase"},
            {"id": "step_db_2", "step_id": "step_2", "name": "Log note", "executor": "frank", "action": "Log note"},
        ]

        briefs = self.module.build_step_briefs(contract, case_steps)

        self.assertEqual([brief["workspace_policy"] for brief in briefs], ["worktree", "scratch"])
        for brief in briefs:
            self.assertFalse(brief["workspace_policy"].startswith("worktree:"))
            self.assertFalse(brief["workspace_policy"].startswith("scratch:"))

    def test_resolve_frank_runtime_defaults_to_native_case_pipeline_and_rejects_invalid_values(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FRANK_RUNTIME", None)
            self.assertEqual(self.module.resolve_frank_runtime(), "native_case_pipeline")
        with patch.dict(os.environ, {"FRANK_RUNTIME": "kanban"}, clear=False):
            with self.assertRaisesRegex(ValueError, "invalid FRANK_RUNTIME"):
                self.module.resolve_frank_runtime()
        with patch.dict(os.environ, {"FRANK_RUNTIME": "direct"}, clear=False):
            with self.assertRaisesRegex(ValueError, "invalid FRANK_RUNTIME"):
                self.module.resolve_frank_runtime()
        with patch.dict(os.environ, {"FRANK_RUNTIME": "sideways"}, clear=False):
            with self.assertRaisesRegex(ValueError, "invalid FRANK_RUNTIME"):
                self.module.resolve_frank_runtime()



    async def test_start_case_execution_default_runtime_uses_native_pipeline(self) -> None:
        client = _FakeClient({}, {"case": {"id": "case_123", "status": "OPEN"}, "steps": [], "contract": {}, "slots": []})
        dispatch_packet = {"case_id": "case_123", "capabilities": {}}

        with patch.object(
            self.module,
            "launch_case_native_pipeline_execution",
            AsyncMock(return_value={"case_id": "case_123", "runtime_mode": "native_case_pipeline", "scheduled": True}),
        ) as launch_native:
            result = await self.module.start_case_execution(client, "case_123", dispatch_packet)

        launch_native.assert_awaited_once()
        self.assertEqual(result["runtime_mode"], "native_case_pipeline")

    async def test_launch_case_native_pipeline_schedules_runner(self) -> None:
        client = _FakeClient({}, {"case": {"id": "case_123", "status": "OPEN"}, "steps": [], "contract": {}, "slots": []})
        dispatch_packet = {
            "case_id": "case_123",
            "runtime": {"mode": "native_case_pipeline", "source_of_truth": "cases/Zenith"},
            "capabilities": {},
        }
        fake_runner = types.SimpleNamespace(create_case_run=AsyncMock(return_value={"id": "case_run_native"}))

        with patch.object(
            self.module, "write_root_context_slots", AsyncMock(return_value={"written": [], "skipped_existing": [], "skipped_missing": []})
        ) as write_slots, patch.object(self.module, "CasePipelineRunner", return_value=fake_runner), patch.object(
            self.module, "schedule_native_case_pipeline_task", AsyncMock(return_value=True)
        ) as schedule_native:
            result = await self.module.launch_case_native_pipeline_execution(
                client,
                "case_123",
                dispatch_packet,
                {"case": {"id": "case_123"}, "contract": {}, "steps": [], "slots": []},
                Path(self.tmpdir.name),
            )

        write_slots.assert_awaited_once()
        fake_runner.create_case_run.assert_awaited_once_with("case_123", dispatch_packet)
        schedule_native.assert_awaited_once()
        self.assertEqual(result["runtime_mode"], "native_case_pipeline")
        self.assertEqual(result["case_run_id"], "case_run_native")
        self.assertEqual(result["launched_steps"], [])

    async def test_recover_native_case_pipelines_schedules_open_native_cases_on_startup(self) -> None:
        class RecoveryClient:
            def __init__(self) -> None:
                self.get_calls: list[tuple[str, dict | None]] = []

            async def get(self, url: str, params: dict | None = None, timeout: float | None = None):
                self.get_calls.append((url, params))
                if url.endswith("/cases"):
                    return _FakeResponse(
                        {
                            "cases": [
                                {
                                    "id": "case_123",
                                    "dispatch_packet_json": json.dumps(
                                        {
                                            "case_id": "case_123",
                                            "runtime": {"mode": "native_case_pipeline", "source_of_truth": "cases/Zenith"},
                                        }
                                    ),
                                },
                                {
                                    "id": "case_kanban",
                                    "dispatch_packet_json": json.dumps({"case_id": "case_kanban", "runtime": {"mode": "kanban"}}),
                                },
                            ]
                        }
                    )
                return _FakeResponse({}, 404)

        client = RecoveryClient()
        with patch.object(self.module, "ensure_case_runtime_dir", return_value=Path(self.tmpdir.name)) as ensure_dir, patch.object(
            self.module, "schedule_native_case_pipeline_task", AsyncMock(return_value=True)
        ) as schedule_native:
            result = await self.module.recover_native_case_pipelines(client, limit=10)

        ensure_dir.assert_called_once_with("case_123")
        schedule_native.assert_awaited_once()
        self.assertEqual(result, {"recovered_case_ids": ["case_123"], "recovered_count": 1})






    async def test_start_case_execution_rejects_invalid_dispatch_runtime_mode(self) -> None:
        client = _FakeClient({}, {"case": {"id": "case_123", "status": "OPEN"}, "steps": [], "contract": {}, "slots": []})
        with self.assertRaisesRegex(ValueError, "invalid dispatch runtime mode"):
            await self.module.start_case_execution(
                client,
                "case_123",
                {"case_id": "case_123", "runtime": {"mode": "sideways"}, "capabilities": {}},
            )

    async def test_dispatch_message_creates_case_stores_packet_and_starts_execution(self) -> None:
        case_payload = {
            "case_id": "case_123",
            "reused": False,
            "status": "OPEN",
            "contract": {
                "process_hash": "hash_123",
                "title": "Process queued review",
                "dispatch_profile": "frank",
                "slot_names": [
                    "review_id",
                    "audio_asset_id",
                    "events_asset_id",
                    "subject_id",
                    "submitted_by",
                    "reviewed_at",
                    "duration_ms",
                    "audio_asset_path",
                    "summary",
                ],
                "variables": {
                    "summary": {"type": "string", "description": "Step output"},
                },
                "steps": [
                    {
                        "step_id": "step_1",
                        "title": "Load review",
                        "instructions": "Load the review.",
                        "executor": "frank",
                        "assignee": "worker",
                        "action": "Load review",
                        "input_items": [
                            {"name": "review_id"},
                            {"name": "audio_asset_id"},
                            {"name": "events_asset_id"},
                        ],
                        "output_variables": ["summary"],
                        "resources": [],
                        "suggested_resources": [],
                        "skills": [],
                    }
                ],
                "dag_edges": [],
                "capabilities": {"env_vars": ["ELEVENLABS_API_KEY"]},
            },
            "steps": [
                {
                    "id": "step_db_1",
                    "step_id": "step_1",
                    "name": "Load review",
                    "executor": "frank",
                    "action": "Load review",
                }
            ],
        }
        case_detail = {
            "case": {
                "id": "case_123",
                "status": "OPEN",
                "dispatch_packet_json": None,
            },
            "contract": case_payload["contract"],
            "steps": case_payload["steps"],
            "slots": [],
            "logs": [],
        }
        client = _FakeClient(case_payload, case_detail)
        msg = {
            "id": "msg_123",
            "event_type": "review_submitted",
            "sender": "tester",
            "message_body": "review_123",
            "payload": {
                "review_id": "review_123",
                "asset_ids": ["events_456", "audio_789"],
                "subject_id": "http://localhost:3000/?reviewMode=on",
                "submitted_by": "Gabriel",
                "stopped_at": "2026-04-29T21:07:12Z",
                "duration_ms": 12000,
            },
        }

        async def fake_start_case_execution(*args, **kwargs):
            client.operations.append(("CALL", "start_case_execution"))
            return {
                "case_id": "case_123",
                "wave_id": "wave_001",
                "launched_steps": [
                    {
                        "step_db_row_id": "step_db_1",
                        "profile": "frank",
                        "session_id": "20260501_010203_abcdef",
                        "session_json_path": "/hub/.hermes/frank_execution/case_123/sessions/session_20260501_010203_abcdef.json",
                        "log_path": "/hub/.hermes/frank_execution/case_123/logs/step_step_db_1.log",
                    }
                ],
            }

        with patch.object(self.module, "start_case_execution", AsyncMock(side_effect=fake_start_case_execution)) as start_mock:
            result = await self.module.dispatch_message(client, msg)

        self.assertEqual(result["case_id"], "case_123")
        self.assertEqual(client.posts[0][0], "http://cases:8083/cases")
        slot_posts = [(url, payload) for url, payload, _ in client.posts if url.endswith("/cases/case_123/slots")]
        self.assertEqual(
            {payload["name"]: payload["value"] for _, payload in slot_posts},
            {
                "review_id": "review_123",
                "audio_asset_id": "audio_789",
                "events_asset_id": "events_456",
                "subject_id": "http://localhost:3000/?reviewMode=on",
                "submitted_by": "Gabriel",
                "reviewed_at": "2026-04-29T21:07:12Z",
                "duration_ms": 12000,
            },
        )
        first_start_index = client.operations.index(("CALL", "start_case_execution"))
        slot_indexes = [idx for idx, op in enumerate(client.operations) if op == ("POST", "http://cases:8083/cases/case_123/slots")]
        self.assertTrue(slot_indexes)
        self.assertLess(max(slot_indexes), first_start_index)
        self.assertIn("http://cases:8083/cases/case_123", client.gets)
        self.assertIn(("http://cases:8083/cases/case_123/dispatch-packet",), tuple((url,) for url, _ in client.puts))
        self.assertFalse(any("/queues/workers/enqueue" in url for url, _, _ in client.posts))
        self.assertFalse(any(url == "http://eventbus:8082/publish" for url, _, _ in client.posts))
        start_mock.assert_awaited_once()

        dispatch_packet = client.puts[0][1]["dispatch_packet_json"]
        self.assertEqual(dispatch_packet["assignment"]["executor"], "frank")
        self.assertEqual(dispatch_packet["assignment"]["dispatch_profile"], "frank")
        self.assertEqual(dispatch_packet["assignment"]["profile_resolution"]["process_default"], "frank")
        self.assertIsNone(dispatch_packet["assignment"]["queue_name"])
        self.assertEqual(dispatch_packet["process_summary"]["title"], "Process queued review")
        self.assertEqual(dispatch_packet["resolved_step_briefs"][0]["instructions"], "Load the review.")
        self.assertEqual(dispatch_packet["resolved_step_briefs"][0]["instruction_source"], "process")
        self.assertEqual(
            dispatch_packet["resolved_step_briefs"][0]["output_schema"],
            {"summary": {"type": "string", "description": "Step output"}},
        )
        self.assertIn(
            "Persist per-step task/runtime state while work is active.",
            dispatch_packet["worker_instructions"],
        )
        self.assertIn(
            "Persist per-step task and runtime state with update_step_runtime_state while work is active.",
            dispatch_packet["worker_execution_rules"],
        )
        self.assertIn("Follow the DAG exactly.", dispatch_packet["worker_instructions"][3])
        self.assertEqual(dispatch_packet["initial_context"]["review_id"], "review_123")
        self.assertEqual(dispatch_packet["initial_context"]["audio_asset_id"], "audio_789")
        self.assertEqual(dispatch_packet["initial_context"]["events_asset_id"], "events_456")
        self.assertEqual(dispatch_packet["capabilities"]["env_vars"], ["ELEVENLABS_API_KEY"])
        self.assertEqual(dispatch_packet["runtime"], {"mode": "native_case_pipeline", "source_of_truth": "cases/Zenith"})
        self.assertEqual(dispatch_packet["initial_context"]["audio_asset_path"], "data/reviews/assets/audio_789")
        self.assertEqual(dispatch_packet["resolved_step_briefs"][0]["workspace_policy"], "scratch")
        self.assertNotIn("hermes_kanban", dispatch_packet)
        self.assertEqual(result["wave_id"], "wave_001")
        self.assertEqual(result["launched_steps"][0]["profile"], "frank")

    async def test_dispatch_message_uses_compiled_resolved_step_briefs_when_available(self) -> None:
        case_payload = {
            "case_id": "case_123",
            "reused": False,
            "status": "OPEN",
            "contract": {
                "process_hash": "hash_123",
                "title": "Process queued review",
                "dispatch_profile": "frank",
                "slot_names": ["review_id", "summary"],
                "variables": {
                    "summary": {"type": "string", "description": "Step output"},
                },
                "root_inputs": ["review_id"],
                "description": "Process a review.",
                "steps": [
                    {
                        "step_id": "step_1",
                        "title": "Load review",
                        "instructions": "",
                        "executor": "frank",
                        "assignee": "worker",
                        "action": "Load review",
                        "input_items": [{"name": "review_id"}],
                        "output_variables": ["summary"],
                        "resources": [],
                        "suggested_resources": [],
                        "skills": [],
                    }
                ],
                "dag_edges": [],
            },
            "steps": [
                {
                    "id": "step_db_1",
                    "step_id": "step_1",
                    "name": "Load review",
                    "executor": "frank",
                    "action": "Load review",
                }
            ],
        }
        case_detail = {
            "case": {"id": "case_123", "status": "OPEN", "dispatch_packet_json": None},
            "contract": case_payload["contract"],
            "steps": case_payload["steps"],
            "slots": [],
            "logs": [],
        }
        client = _FakeClient(case_payload, case_detail)
        msg = {
            "id": "msg_123",
            "event_type": "review_submitted",
            "sender": "tester",
            "message_body": "review_123",
            "payload": {"review_id": "review_123"},
        }

        with patch.object(
            self.module,
            "resolve_brief_compiler_config",
            return_value=self.module.BriefCompilerConfig(
                url="http://example.invalid/chat/completions",
                headers={},
                model="test-model",
            ),
        ), patch.object(
            self.module,
            "_run_brief_compiler_prompt",
            AsyncMock(
                return_value={
                    "process_summary": {"execution_summary": "Load the review metadata and normalize it."},
                    "resolved_step_briefs": [
                        {
                            "step_id": "step_1",
                            "instructions": "Load and normalize the review metadata before producing summary.",
                            "tasking_guidance": ["Confirm required inputs are present first."],
                        }
                    ],
                    "worker_execution_rules": ["Keep runtime state updated while tasks are active."],
                }
            ),
        ), patch.object(
            self.module,
            "start_case_execution",
            AsyncMock(return_value={"case_id": "case_123", "wave_id": None, "launched_steps": []}),
        ):
            await self.module.dispatch_message(client, msg)

        dispatch_packet = client.puts[0][1]["dispatch_packet_json"]
        self.assertEqual(
            dispatch_packet["resolved_step_briefs"][0]["instructions"],
            "Load and normalize the review metadata before producing summary.",
        )
        self.assertEqual(dispatch_packet["resolved_step_briefs"][0]["tasking_guidance"], ["Confirm required inputs are present first."])
        self.assertEqual(dispatch_packet["resolved_step_briefs"][0]["instruction_source"], "compiled")
        self.assertEqual(
            dispatch_packet["process_summary"]["execution_summary"],
            "Load the review metadata and normalize it.",
        )
        self.assertIn(
            "Keep runtime state updated while tasks are active.",
            dispatch_packet["worker_execution_rules"],
        )

    async def test_dispatch_message_reuses_existing_assignment_without_enqueuing_duplicate_work(self) -> None:
        assignment_id = "assignment:case_123:frank"
        case_payload = {
            "case_id": "case_123",
            "reused": True,
            "status": "IN_PROGRESS",
            "contract": {
                "process_hash": "hash_123",
                "title": "Process queued review",
                "dispatch_profile": "frank",
                "slot_names": ["review_id"],
                "steps": [
                    {
                        "step_id": "step_1",
                        "title": "Load review",
                        "executor": "frank",
                        "assignee": "worker",
                        "action": "Load review",
                        "input_items": [{"name": "review_id"}],
                        "output_variables": [],
                        "resources": [],
                        "skills": [],
                    }
                ],
                "dag_edges": [],
            },
            "steps": [
                {
                    "id": "step_db_1",
                    "step_id": "step_1",
                    "name": "Load review",
                    "executor": "frank",
                    "action": "Load review",
                }
            ],
        }
        case_detail = {
            "case": {
                "id": "case_123",
            "status": "IN_PROGRESS",
            "dispatch_packet_json": {
                "assignment": {
                    "assignment_id": assignment_id,
                }
            },
            },
            "contract": case_payload["contract"],
            "steps": case_payload["steps"],
            "slots": [],
            "logs": [],
        }
        client = _FakeClient(case_payload, case_detail)
        msg = {
            "id": "msg_123",
            "event_type": "review_submitted",
            "sender": "tester",
            "payload": {},
        }

        with patch.object(self.module, "start_case_execution", AsyncMock(return_value={"case_id": "case_123", "wave_id": "wave_resume", "launched_steps": []})) as start_mock:
            result = await self.module.dispatch_message(client, msg)

        self.assertEqual(result["assignment_id"], assignment_id)
        start_mock.assert_awaited_once()
        self.assertFalse(any("/queues/workers/enqueue" in url for url, _, _ in client.posts))
        self.assertFalse(any(url == "http://cases:8083/cases/case_123/status" for url, _ in client.puts))
        self.assertFalse(any(url == "http://eventbus:8082/publish" for url, _, _ in client.posts))


    async def test_start_case_execution_does_not_duplicate_durable_active_steps_after_restart(self) -> None:
        case_detail = {
            "case": {"id": "case_123", "status": "IN_PROGRESS"},
            "contract": {"steps": [{"step_id": "step_1", "input_items": [], "output_variables": []}]},
            "steps": [
                {
                    "id": "step_db_1",
                    "step_id": "step_1",
                    "name": "No output side effect",
                    "executor": "frank",
                    "status": "RUNNING",
                    "runtime_state_json": {"status": "active", "agent_run_id": "run_existing"},
                }
            ],
            "slots": [],
            "logs": [],
        }
        client = _FakeClient({}, case_detail)

        result = await self.module.start_case_execution(client, "case_123", {"case_id": "case_123", "capabilities": {}})

        self.assertTrue(result["already_active"])
        self.assertEqual(result["launched_steps"], [])
        self.assertFalse(any("/status" in url and payload == {"status": "BLOCKED"} for url, payload in client.puts))
    async def test_start_case_execution_blocks_missing_required_env_before_launch(self) -> None:
        case_detail = {
            "case": {"id": "case_123", "status": "OPEN"},
            "contract": {"steps": [{"step_id": "step_1", "input_items": [], "output_variables": []}]},
            "steps": [
                {
                    "id": "step_db_1",
                    "step_id": "step_1",
                    "name": "Transcribe audio",
                    "executor": "frank",
                    "status": "PENDING",
                    "runtime_state_json": {},
                }
            ],
            "slots": [],
            "logs": [],
        }
        client = _FakeClient({}, case_detail)
        dispatch_packet = {
            "case_id": "case_123",
            "event_type": "review_submitted",
            "capabilities": {"env_vars": ["ELEVENLABS_API_KEY"]},
        }

        with patch.dict(os.environ, {}, clear=True):
            result = await self.module.start_case_execution(client, "case_123", dispatch_packet)

        self.assertEqual(result["launched_steps"], [])
        self.assertIn("ELEVENLABS_API_KEY", result["blocked_reason"])
        self.assertIn(("http://cases:8083/cases/case_123/status", {"status": "BLOCKED"}), client.puts)


    async def test_write_root_context_slots_skips_existing_non_empty_values(self) -> None:
        case_detail = {
            "case": {"id": "case_123"},
            "contract": {
                "slot_names": [
                    "review_id",
                    "audio_asset_id",
                    "events_asset_id",
                    "subject_id",
                    "submitted_by",
                    "reviewed_at",
                    "duration_ms",
                ]
            },
            "steps": [],
            "slots": [
                {"name": "review_id", "value": "review_existing"},
                {"name": "audio_asset_id", "value": "audio_existing"},
                {"name": "events_asset_id", "value": "events_existing"},
                {"name": "subject_id", "value": "subject_existing"},
                {"name": "submitted_by", "value": "Gabriel"},
                {"name": "reviewed_at", "value": "2026-04-29T21:07:12Z"},
                {"name": "duration_ms", "value": "12000"},
            ],
        }
        client = _FakeClient({}, case_detail)
        dispatch_packet = {
            "initial_context": {
                "review_id": "review_changed",
                "audio_asset_id": "audio_changed",
                "events_asset_id": "events_changed",
                "subject_id": "subject_changed",
                "submitted_by": "Someone Else",
                "reviewed_at": "2026-05-01T00:00:00Z",
                "duration_ms": 999,
            }
        }

        result = await self.module.write_root_context_slots(client, "case_123", dispatch_packet, case_detail)

        self.assertEqual(result["written"], [])
        self.assertEqual(set(result["skipped_existing"]), set(self.module.REVIEW_ROOT_CONTEXT_SLOT_NAMES))
        self.assertFalse(any(url.endswith("/cases/case_123/slots") for url, _, _ in client.posts))


    def test_expected_eventbus_disconnects_are_classified_without_stacktrace(self) -> None:
        self.assertTrue(self.module.is_expected_eventbus_disconnect(httpx.RemoteProtocolError("stream closed")))
        self.assertTrue(self.module.is_expected_eventbus_disconnect(httpx.ReadError("read failed")))
        self.assertFalse(self.module.is_expected_eventbus_disconnect(RuntimeError("boom")))

    async def test_handle_enqueued_acks_only_after_successful_dispatch(self) -> None:
        msg = {"id": "msg_1", "event_type": "review_submitted", "sender": "tester", "payload": {}}
        client = object()

        with patch.object(self.module, "dequeue", AsyncMock(return_value=msg)), \
             patch.object(self.module, "dispatch_message", AsyncMock(return_value={"case_id": "case_1"})) as dispatch_mock, \
             patch.object(self.module, "ack", AsyncMock()) as ack_mock, \
             patch.object(self.module, "nack", AsyncMock()) as nack_mock:
            await self.module.handle_enqueued(client)

        dispatch_mock.assert_awaited_once()
        ack_mock.assert_awaited_once_with(client, "msg_1", {"case_id": "case_1"})
        nack_mock.assert_not_called()

    async def test_handle_enqueued_nacks_on_launch_failure(self) -> None:
        msg = {"id": "msg_2", "event_type": "review_submitted", "sender": "tester", "payload": {}}
        client = object()

        with patch.object(self.module, "dequeue", AsyncMock(return_value=msg)), \
             patch.object(self.module, "dispatch_message", AsyncMock(side_effect=RuntimeError("launch failed"))), \
             patch.object(self.module, "ack", AsyncMock()) as ack_mock, \
             patch.object(self.module, "nack", AsyncMock()) as nack_mock:
            await self.module.handle_enqueued(client)

        ack_mock.assert_not_called()
        nack_mock.assert_awaited_once()

    async def test_handle_enqueued_processes_only_one_message_per_wake(self) -> None:
        first = {"id": "msg_1", "event_type": "review_submitted", "sender": "tester", "payload": {}}
        second = {"id": "msg_2", "event_type": "review_submitted", "sender": "tester", "payload": {}}
        client = object()

        with patch.object(self.module, "dequeue", AsyncMock(side_effect=[first, second])) as dequeue_mock, \
             patch.object(self.module, "dispatch_message", AsyncMock(return_value={"case_id": "case_1"})) as dispatch_mock, \
             patch.object(self.module, "ack", AsyncMock()) as ack_mock, \
             patch.object(self.module, "nack", AsyncMock()) as nack_mock:
            await self.module.handle_enqueued(client)

        self.assertEqual(dequeue_mock.await_count, 1)
        dispatch_mock.assert_awaited_once_with(client, first)
        ack_mock.assert_awaited_once_with(client, "msg_1", {"case_id": "case_1"})
        nack_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
