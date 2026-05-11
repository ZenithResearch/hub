from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    def __init__(self, message: dict | None, case_detail: dict):
        self.message = message
        self.case_detail = case_detail
        self.posts: list[tuple[str, dict | None, dict | None]] = []
        self.puts: list[tuple[str, dict | None]] = []
        self.gets: list[str] = []

    async def post(self, url: str, json: dict | None = None, params: dict | None = None, timeout: float | None = None):
        self.posts.append((url, json, params))
        if url.endswith("/dequeue"):
            return _FakeResponse({"found": self.message is not None, "message": self.message}, 200)
        if url.endswith("/ack"):
            return _FakeResponse({"ok": True}, 200)
        if url.endswith("/nack"):
            return _FakeResponse({"ok": True, "new_status": "pending"}, 200)
        return _FakeResponse({"ok": True, "log_id": "log_123"}, 201)

    async def get(self, url: str, timeout: float | None = None):
        self.gets.append(url)
        return _FakeResponse(self.case_detail, 200)

    async def put(self, url: str, json: dict | None = None, timeout: float | None = None):
        self.puts.append((url, json))
        return _FakeResponse({"ok": True}, 200)


class HermesWorkerQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        (root / ".hermes/logs").mkdir(parents=True)
        canonical_skills = root / "canonical_skills"
        case_skill = canonical_skills / "case-execution-loop"
        step_skill = canonical_skills / "step-execution-loop"
        case_skill.mkdir(parents=True)
        step_skill.mkdir(parents=True)
        (case_skill / "scripts").mkdir(parents=True)
        (case_skill / "SKILL.md").write_text(
            "---\nname: case-execution-loop\ndescription: test\nversion: \"1.0.0\"\n---\n\n# case-execution-loop\n\nUse update_step_runtime_state while steps are active.\n",
            encoding="utf-8",
        )
        (case_skill / "scripts" / "fetch_review_assets.py").write_text(
            "print('ok')\n",
            encoding="utf-8",
        )
        (step_skill / "SKILL.md").write_text(
            "---\nname: step-execution-loop\ndescription: test\nversion: \"1.0.0\"\n---\n\n# step-execution-loop\n\nWrite every declared output slot exactly once.\n",
            encoding="utf-8",
        )

        os.environ["QUEUE_HTTP_URL"] = "http://queue:8081"
        os.environ["CASES_HTTP_URL"] = "http://cases:8083"
        os.environ["EVENTBUS_URL"] = "http://eventbus:8082"
        os.environ["HERMES_HOME"] = str(root / ".hermes")
        os.environ["HERMES_CANONICAL_SKILLS_DIR"] = str(canonical_skills)
        os.environ["TERMINAL_CWD"] = str(root)
        os.environ["HERMES_FORWARD_CASES_HTTP_URL"] = "http://host.docker.internal:8083"
        os.environ["GATEWAY_HTTP_URL"] = "http://gateway-http:8080"
        os.environ["WORKER_QUEUE_NAME"] = "workers"
        os.environ["WORKER_ID"] = "hermes-worker"
        os.environ.pop("HERMES_DISPATCH_PROVIDER", None)
        os.environ.pop("HERMES_DISPATCH_MODEL", None)
        os.environ.pop("HERMES_DISPATCH_AUX_PROVIDER", None)
        os.environ.pop("HERMES_DISPATCH_AUX_MODEL", None)

        sys.modules.pop("services.hermes_worker_queue.main", None)
        module = importlib.import_module("services.hermes_worker_queue.main")
        self.module = importlib.reload(module)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    async def test_worker_claims_assignment_launches_hermes_and_acks(self) -> None:
        message = {
            "id": "msg_worker_1",
            "payload": {
                "case_id": "case_123",
                "assignment_id": "assignment:case_123:frank",
                "dispatch_profile": "frank",
                "executor": "frank",
            },
        }
        case_detail = {
            "case": {
                "id": "case_123",
                "status": "READY",
                "dispatch_packet_json": {
                    "process_summary": {
                        "title": "Process queued review",
                        "description": "Process a review.",
                    },
                    "initial_context": {
                        "review_id": "review_123",
                    },
                    "worker_instructions": [
                        "Write the initial slot values before executing steps.",
                    ],
                    "worker_execution_rules": [
                        "Do not redefine the DAG.",
                        "Persist per-step task and runtime state with update_step_runtime_state while work is active.",
                    ],
                    "resolved_step_briefs": [
                        {
                            "step_db_row_id": "step_db_1",
                            "step_id": "step_1",
                            "title": "Load review",
                            "instructions": "Load the review.",
                            "outputs": ["summary"],
                        }
                    ],
                    "assignment": {
                        "assignment_id": "assignment:case_123:frank",
                        "dispatch_profile": "frank",
                        "policy": {
                            "required_skills": ["transcribe-review-audio"],
                            "allowed_tools": ["get_case"],
                            "denied_tools": [],
                            "resource_scopes": ["review assets workspace"],
                        },
                    }
                },
            }
        }
        client = _FakeClient(message, case_detail)

        with patch.object(
            self.module,
            "prepare_profile_skills",
            return_value={
                "source_home": str((Path(os.environ["HERMES_HOME"]) / "profiles" / "frank").resolve()),
                "runtime_home": str((Path(os.environ["HERMES_HOME"]) / "dispatch_runtime" / "frank").resolve()),
                "model_provider": "openrouter",
                "model_name": "google/gemma-4-31b-it",
                "aux_provider": "main",
                "aux_model_name": "google/gemma-4-31b-it",
                "skills_home": str((Path(os.environ["HERMES_HOME"]) / "profiles" / "frank").resolve()),
                "skills_loaded": ["case-execution-loop", "step-execution-loop"],
                "skills_synced_homes": [str(Path(os.environ["HERMES_HOME"]).resolve())],
                "asset_fetch_helper_path": str(
                    (
                        Path(os.environ["HERMES_HOME"])
                        / "dispatch_runtime"
                        / "frank"
                        / "skills"
                        / "worker"
                        / "case-execution-loop"
                        / "scripts"
                        / "fetch_review_assets.py"
                    ).resolve()
                ),
            },
        ) as prepare_mock, patch.object(
            self.module,
            "launch_hermes_session",
            return_value=(
                SimpleNamespace(pid=42, poll=lambda: None),
                {
                    "pid": 42,
                    "command": ["hermes", "-p", "frank", "chat", "-q", "worker prompt"],
                    "log_path": "/tmp/case_123.log",
                },
            ),
        ) as launch_mock:
            def _fake_create_task(coro):
                coro.close()
                return SimpleNamespace(add_done_callback=lambda callback: None)

            with patch.object(self.module.asyncio, "create_task", side_effect=_fake_create_task), patch.object(
                self.module, "_track_background_task", return_value=None
            ):
                await self.module.handle_assignment(client)

        prepare_mock.assert_called_once_with("frank")
        launch_mock.assert_called_once_with(
            "case_123",
            "frank",
            case_detail["case"]["dispatch_packet_json"],
            str((Path(os.environ["HERMES_HOME"]) / "dispatch_runtime" / "frank").resolve()),
        )
        self.assertIn("http://cases:8083/cases/case_123", client.gets)
        self.assertTrue(any(url == "http://cases:8083/cases/case_123/status" and json == {"status": "IN_PROGRESS"} for url, json in client.puts))
        self.assertTrue(any(url.endswith("/ack") for url, _, _ in client.posts))
        messages = [payload["message"] for url, payload, _ in client.posts if url.endswith("/logs")]
        self.assertIn("worker assignment claimed", messages)
        self.assertIn("worker profile resolved", messages)
        self.assertIn("worker skills prepared", messages)
        self.assertIn("worker execution started", messages)
        execution_logs = [payload for url, payload, _ in client.posts if url.endswith("/logs") and payload["message"] == "worker execution started"]
        self.assertEqual(len(execution_logs), 1)
        execution_metadata = execution_logs[0]["metadata"]
        self.assertNotIn("prompt_preview", execution_metadata)
        self.assertNotIn("worker prompt", repr(execution_metadata))
        self.assertEqual(execution_metadata["command"], ["hermes", "-p", "frank", "chat", "-q", "[REDACTED_PROMPT]"])

    async def test_worker_acks_duplicate_assignment_when_case_already_in_progress(self) -> None:
        message = {
            "id": "msg_worker_1",
            "payload": {
                "case_id": "case_123",
                "assignment_id": "assignment:case_123:frank",
                "dispatch_profile": "frank",
                "executor": "frank",
            },
        }
        case_detail = {
            "case": {
                "id": "case_123",
                "status": "IN_PROGRESS",
                "dispatch_packet_json": {
                    "assignment": {
                        "assignment_id": "assignment:case_123:frank",
                        "dispatch_profile": "frank",
                    }
                },
            }
        }
        client = _FakeClient(message, case_detail)

        with patch.object(self.module, "launch_hermes_session") as launch_mock:
            await self.module.handle_assignment(client)

        launch_mock.assert_not_called()
        self.assertFalse(any(url == "http://cases:8083/cases/case_123/status" for url, _ in client.puts))
        self.assertTrue(any(url.endswith("/ack") for url, _, _ in client.posts))
        messages = [payload["message"] for url, payload, _ in client.posts if url.endswith("/logs")]
        self.assertIn("duplicate worker assignment ignored", messages)

    async def test_worker_nacks_when_skill_preflight_fails(self) -> None:
        message = {
            "id": "msg_worker_1",
            "payload": {
                "case_id": "case_123",
                "assignment_id": "assignment:case_123:frank",
                "dispatch_profile": "frank",
                "executor": "frank",
            },
        }
        case_detail = {
            "case": {
                "id": "case_123",
                "status": "READY",
                "dispatch_packet_json": {
                    "assignment": {
                        "assignment_id": "assignment:case_123:frank",
                        "dispatch_profile": "frank",
                    }
                },
            }
        }
        client = _FakeClient(message, case_detail)

        with patch.object(
            self.module,
            "prepare_profile_skills",
            side_effect=RuntimeError("skill preload failed"),
        ) as prepare_mock, patch.object(self.module, "launch_hermes_session") as launch_mock:
            await self.module.handle_assignment(client)

        prepare_mock.assert_called_once_with("frank")
        launch_mock.assert_not_called()
        self.assertFalse(any(url == "http://cases:8083/cases/case_123/status" for url, _ in client.puts))
        self.assertTrue(any(url.endswith("/nack") for url, _, _ in client.posts))
        self.assertFalse(any(url.endswith("/ack") for url, _, _ in client.posts))
        messages = [payload["message"] for url, payload, _ in client.posts if url.endswith("/logs")]
        self.assertIn("worker assignment failed", messages)

    def test_build_hermes_command_uses_native_profile_flag(self) -> None:
        prompt = "execute case_123"
        self.assertEqual(
            self.module.build_hermes_command("case_123", "frank", prompt),
            [
                "hermes",
                "--skills",
                "case-execution-loop,step-execution-loop",
                "chat",
                "-q",
                prompt,
            ],
        )
        self.assertEqual(
            self.module.build_hermes_command("case_456", None, prompt),
            [
                "hermes",
                "--skills",
                "case-execution-loop,step-execution-loop",
                "chat",
                "-q",
                prompt,
            ],
        )

    def test_launch_hermes_session_returns_prompt_safe_metadata(self) -> None:
        dispatch_packet = {
            "process_summary": {"title": "Process queued review"},
            "assignment": {"assignment_id": "assignment:case_123:frank", "dispatch_profile": "frank"},
            "initial_context": {"secret_review_prompt": "DO_NOT_LOG_PROMPT_CONTENT"},
            "resolved_step_briefs": [{"step_id": "step_1", "instructions": "DO_NOT_LOG_PROMPT_CONTENT"}],
        }
        captured = {}

        class FakeProcess:
            pid = 4242

        def fake_popen(command, cwd=None, env=None, stdout=None, stderr=None, start_new_session=None):
            captured["command"] = command
            return FakeProcess()

        with patch.object(self.module.subprocess, "Popen", side_effect=fake_popen):
            _, launch_result = self.module.launch_hermes_session(
                "case_123",
                "frank",
                dispatch_packet,
                str(Path(os.environ["HERMES_HOME"]).resolve()),
            )

        self.assertTrue(any("DO_NOT_LOG_PROMPT_CONTENT" in str(part) for part in captured["command"]))
        self.assertNotIn("prompt_preview", launch_result)
        self.assertNotIn("DO_NOT_LOG_PROMPT_CONTENT", repr(launch_result))
        self.assertEqual(launch_result["command"], ["hermes", "--skills", "case-execution-loop,step-execution-loop", "chat", "-q", "[REDACTED_PROMPT]"])
        self.assertGreater(launch_result["prompt_length"], 0)
        self.assertRegex(launch_result["prompt_sha256"], r"^[0-9a-f]{64}$")

    def test_worker_execution_started_log_redacts_launch_prompt_metadata(self) -> None:
        unsafe_launch_result = {
            "pid": 42,
            "command": ["hermes", "chat", "-q", "DO_NOT_LOG_PROMPT_CONTENT"],
            "prompt_preview": "DO_NOT_LOG_PROMPT_CONTENT",
            "prompt_sha256": "a" * 64,
            "prompt_length": 25,
            "log_path": "/tmp/case_123.log",
        }
        metadata = self.module.safe_launch_log_metadata(unsafe_launch_result)

        self.assertNotIn("prompt_preview", metadata)
        self.assertNotIn("DO_NOT_LOG_PROMPT_CONTENT", repr(metadata))
        self.assertEqual(metadata["command"], ["hermes", "chat", "-q", "[REDACTED_PROMPT]"])
        self.assertEqual(metadata["prompt_sha256"], "a" * 64)
        self.assertEqual(metadata["prompt_length"], 25)

    def test_prepare_profile_skills_materializes_dispatch_runtime_home_with_codex_without_endpoint_fields(self) -> None:
        source_home = Path(os.environ["HERMES_HOME"]) / "profiles" / "frank"
        source_home.mkdir(parents=True, exist_ok=True)
        stale_bridge_url = "http://" + "host.docker.internal" + ":3690/v1"
        (source_home / "config.yaml").write_text(
            (
                "model:\n"
                "  provider: custom\n"
                f"  base_url: {stale_bridge_url}\n"
                "  api_key: none\n"
                "  model: moonshotai/kimi-k2.6\n"
                "auxiliary:\n"
                "  provider: custom\n"
                f"  base_url: {stale_bridge_url}\n"
                "  api_key: none\n"
                "  model: moonshotai/kimi-k2.6\n"
            ),
            encoding="utf-8",
        )

        with patch.object(
            self.module,
            "validate_preloaded_skills_for_home",
            return_value={
                "hermes_home": str((Path(os.environ["HERMES_HOME"]) / "dispatch_runtime" / "frank").resolve()),
                "loaded": ["case-execution-loop", "step-execution-loop"],
                "missing": [],
            },
        ):
            result = self.module.prepare_profile_skills("frank")

        runtime_home = Path(result["runtime_home"])
        self.assertEqual(result["model_provider"], "openai-codex")
        self.assertEqual(result["model_name"], "gpt-5.3-codex")
        self.assertTrue((runtime_home / "skills" / "worker" / "case-execution-loop" / "SKILL.md").exists())
        self.assertTrue((runtime_home / "skills" / "worker" / "step-execution-loop" / "SKILL.md").exists())
        self.assertTrue((runtime_home / "skills" / "worker" / "case-execution-loop" / "scripts" / "fetch_review_assets.py").exists())
        config = (runtime_home / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("gpt-5.3-codex", config)
        self.assertIn("provider: openai-codex", config)
        self.assertNotIn("host.docker.internal" + ":3690", config)
        self.assertNotIn("base_url:", config)
        self.assertNotIn("api_key:", config)

    def test_apply_dispatch_model_config_preserves_openrouter_credentials_except_stale_local_endpoint(self) -> None:
        stale_bridge_url = "http://" + "host.docker.internal" + ":3690/v1"
        sanitized = self.module._apply_dispatch_model_config(
            {
                "provider": "custom",
                "model": "legacy",
                "base_url": stale_bridge_url,
                "api_key": "keep-me",
            },
            provider="openrouter",
            model="openrouter/model",
        )
        self.assertEqual(sanitized["provider"], "openrouter")
        self.assertEqual(sanitized["model"], "openrouter/model")
        self.assertNotIn("base_url", sanitized)
        self.assertEqual(sanitized["api_key"], "keep-me")

        preserved = self.module._apply_dispatch_model_config(
            {
                "provider": "custom",
                "model": "legacy",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "keep-me",
            },
            provider="openrouter",
            model="openrouter/model",
        )
        self.assertEqual(preserved["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(preserved["api_key"], "keep-me")

    def test_apply_dispatch_model_config_preserves_custom_and_openai_endpoint_fields(self) -> None:
        for provider in ("custom", "openai"):
            with self.subTest(provider=provider):
                updated = self.module._apply_dispatch_model_config(
                    {
                        "base_url": "https://example.invalid/v1",
                        "api_key": "explicit-key",
                    },
                    provider=provider,
                    model="selected-model",
                )
                self.assertEqual(updated["base_url"], "https://example.invalid/v1")
                self.assertEqual(updated["api_key"], "explicit-key")

    def test_apply_dispatch_model_config_treats_main_as_auxiliary_alias_without_endpoint_fields(self) -> None:
        updated = self.module._apply_dispatch_model_config(
            {
                "provider": "custom",
                "base_url": "https://example.invalid/v1",
                "api_key": "copied-key",
            },
            provider="main",
            model="",
        )
        self.assertEqual(updated, {"provider": "main", "model": ""})

    def test_build_worker_prompt_includes_process_summary_and_step_briefs(self) -> None:
        prompt = self.module.build_worker_prompt(
            "case_123",
            {
                "process_summary": {"title": "Process queued review", "description": "Process a review."},
                "assignment": {"assignment_id": "assignment:case_123:frank", "dispatch_profile": "frank"},
                "initial_context": {"review_id": "review_123"},
                "worker_instructions": ["Write the initial slot values before executing steps."],
                "worker_execution_rules": ["Do not redefine the DAG."],
                "resolved_step_briefs": [{"step_id": "step_1", "instructions": "Load the review."}],
            },
            "frank",
        )
        self.assertIn("Process queued review", prompt)
        self.assertIn('"review_id": "review_123"', prompt)
        self.assertIn('"step_id": "step_1"', prompt)
        self.assertIn("Do not redefine the DAG.", prompt)
        self.assertIn("fetch_review_assets.py", prompt)
        self.assertIn("worker_cli.py", prompt)
        self.assertIn("http://gateway-http:8080", prompt)
        self.assertIn("do not guess under /hub/data/reviews/assets", prompt)
        self.assertIn("Run Step 1 locally in the parent worker", prompt)
        self.assertIn("Delegated step runners must return structured JSON only", prompt)
        self.assertIn("Use the preloaded case-execution-loop and step-execution-loop skills as the authoritative operational procedure.", prompt)
        self.assertNotIn("Profile skill documents:", prompt)
        self.assertNotIn("Use update_step_runtime_state while steps are active.", prompt)

    def test_default_canonical_skills_dir_points_to_frank_worker_skills(self) -> None:
        previous = os.environ.pop("HERMES_CANONICAL_SKILLS_DIR", None)
        try:
            sys.modules.pop("services.hermes_worker_queue.main", None)
            module = importlib.import_module("services.hermes_worker_queue.main")
            module = importlib.reload(module)
            self.assertEqual(
                module.CANONICAL_SKILLS_DIR,
                Path("/hub/rolodex/agents/frank/skills/worker"),
            )
        finally:
            if previous is not None:
                os.environ["HERMES_CANONICAL_SKILLS_DIR"] = previous
            sys.modules.pop("services.hermes_worker_queue.main", None)
            self.module = importlib.reload(importlib.import_module("services.hermes_worker_queue.main"))

    def test_sync_repo_skills_installs_managed_skill_directories(self) -> None:
        installed = self.module.sync_repo_skills()
        self.assertEqual(
            installed[str(Path(os.environ["HERMES_HOME"]).resolve())],
            ["case-execution-loop", "step-execution-loop"],
        )
        self.assertTrue((Path(os.environ["HERMES_HOME"]) / "skills" / "worker" / "case-execution-loop" / "SKILL.md").exists())
        self.assertTrue((Path(os.environ["HERMES_HOME"]) / "skills" / "worker" / "step-execution-loop" / "SKILL.md").exists())

    def test_sync_repo_skills_installs_into_selected_profile_home(self) -> None:
        installed = self.module.sync_repo_skills("frank")
        profile_home = Path(os.environ["HERMES_HOME"]) / "profiles" / "frank"
        self.assertEqual(
            installed[str(profile_home.resolve())],
            ["case-execution-loop", "step-execution-loop"],
        )
        self.assertTrue((profile_home / "skills" / "worker" / "case-execution-loop" / "SKILL.md").exists())
        self.assertTrue((profile_home / "skills" / "worker" / "step-execution-loop" / "SKILL.md").exists())

    def test_validate_preloaded_skills_uses_selected_profile_home(self) -> None:
        recorded = {}

        def fake_run(cmd, cwd=None, env=None, capture_output=None, text=None, timeout=None):
            recorded["cmd"] = cmd
            recorded["cwd"] = cwd
            recorded["env"] = env
            class Result:
                returncode = 0
                stdout = '{"loaded":["case-execution-loop","step-execution-loop"],"missing":[]}\n'
                stderr = ""
            return Result()

        with patch.object(self.module.subprocess, "run", side_effect=fake_run):
            result = self.module.validate_preloaded_skills("frank")

        self.assertEqual(
            result["hermes_home"],
            str((Path(os.environ["HERMES_HOME"]) / "profiles" / "frank").resolve()),
        )
        self.assertEqual(result["loaded"], ["case-execution-loop", "step-execution-loop"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(
            recorded["env"]["HERMES_HOME"],
            str((Path(os.environ["HERMES_HOME"]) / "profiles" / "frank").resolve()),
        )

    async def test_monitor_worker_session_logs_session_artifact_and_exit(self) -> None:
        runtime_home = Path(os.environ["HERMES_HOME"]) / "dispatch_runtime" / "frank"
        sessions_dir = runtime_home / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "session_20260430_123456_test.json").write_text(
            '{"session_id":"20260430_123456_test","model":"google/gemma-4-31b-it","base_url":"https://openrouter.ai/api/v1"}',
            encoding="utf-8",
        )

        fake_process = SimpleNamespace()
        poll_values = iter([None, 0])
        fake_process.poll = lambda: next(poll_values, 0)

        captured: list[tuple[str, str, str, dict | None]] = []

        async def fake_append(client, case_id, log_type, message, *, metadata=None):
            captured.append((case_id, log_type, message, metadata))

        with patch.object(self.module, "append_case_log_safe", side_effect=fake_append):
            await self.module.monitor_worker_session(
                "case_123",
                "assignment:case_123:frank",
                "frank",
                str(runtime_home),
                "/tmp/case_123.log",
                fake_process,
            )

        self.assertEqual(captured[0][2], "worker session artifact ready")
        self.assertEqual(captured[0][3]["session_id"], "20260430_123456_test")
        self.assertEqual(captured[0][3]["session_export_format"], "hermes-session-json")
        self.assertEqual(captured[1][2], "worker execution exited")
        self.assertEqual(captured[1][3]["returncode"], 0)

    def test_expected_eventbus_disconnects_are_classified_without_stacktrace(self) -> None:
        self.assertTrue(self.module.is_expected_eventbus_disconnect(httpx.RemoteProtocolError("stream closed")))
        self.assertTrue(self.module.is_expected_eventbus_disconnect(httpx.ReadError("read failed")))
        self.assertFalse(self.module.is_expected_eventbus_disconnect(RuntimeError("boom")))


if __name__ == "__main__":
    unittest.main()
