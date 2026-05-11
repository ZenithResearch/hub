from __future__ import annotations

import ast
import subprocess
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"


class Phase11DockerE2EContractTests(unittest.TestCase):
    def _compose(self) -> dict:
        return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    def test_frank_native_runtime_has_no_alternate_runtime_mode_wiring(self) -> None:
        frank_env = self._compose()["services"]["frank"]["environment"]

        self.assertEqual(frank_env["HERMES_HOME"], "/hub/rolodex/agents/frank")
        self.assertNotIn("FRANK_RUNTIME", frank_env)
        self.assertNotIn("FRANK_KANBAN_HERMES_HOME", frank_env)
        self.assertNotIn("FRANK_KANBAN_STEP_SPAWN_MODE", frank_env)
        self.assertEqual(frank_env["TOOL_DIR"], "${TOOL_DIR:-/app/libs/tools}")
        self.assertEqual(frank_env["GATEWAY_HTTP_URL"], "${GATEWAY_HTTP_URL:-http://gateway-http:8080}")
        self.assertEqual(frank_env["HUB_CONFIG_SECRETS_PATH"], "/hub/.hermes/config-secrets.env")

    def test_gateway_and_worker_share_the_same_hermes_home_mount(self) -> None:
        services = self._compose()["services"]
        gateway_env = services["gateway-http"]["environment"]
        worker_env = services["hermes-worker-queue"]["environment"]

        self.assertEqual(gateway_env["HERMES_HOME"], "/hub/.hermes")
        self.assertEqual(worker_env["HERMES_HOME"], "/hub/.hermes")
        for service_name in ("frank", "gateway-http", "hermes-worker-queue"):
            self.assertIn("./.hermes:/hub/.hermes", services[service_name]["volumes"])

    def test_worker_queue_runtime_files_referenced_by_compose_are_tracked(self) -> None:
        expected_paths = [
            "docker/hermes_worker_queue/Dockerfile",
            "docker/stt_http/Dockerfile",
            "services/stt_http/main.py",
            "services/hermes_worker_queue/main.py",
            "tests/test_hermes_worker_queue.py",
            "tests/test_local_whisper_tool.py",
            "rolodex/agents/frank/skills/worker/case-execution-loop/SKILL.md",
            "rolodex/agents/frank/skills/worker/case-execution-loop/scripts/fetch_review_assets.py",
            "rolodex/agents/frank/skills/worker/case-execution-loop/scripts/worker_cli.py",
            "rolodex/agents/frank/skills/worker/step-execution-loop/SKILL.md",
        ]
        tracked = set(
            subprocess.check_output(
                ["git", "ls-files", *expected_paths],
                cwd=REPO_ROOT,
                text=True,
            ).splitlines()
        )
        self.assertEqual(tracked, set(expected_paths))

    def test_local_stt_dependency_is_isolated_to_stt_http_image(self) -> None:
        frank_dockerfile = (REPO_ROOT / "docker/frank/Dockerfile").read_text(encoding="utf-8")
        worker_dockerfile = (REPO_ROOT / "docker/hermes_worker_queue/Dockerfile").read_text(encoding="utf-8")
        stt_dockerfile = (REPO_ROOT / "docker/stt_http/Dockerfile").read_text(encoding="utf-8")

        self.assertNotIn("openai-whisper", frank_dockerfile)
        self.assertNotIn("openai-whisper", worker_dockerfile)
        self.assertNotIn("ffmpeg", frank_dockerfile)
        self.assertNotIn("ffmpeg", worker_dockerfile)
        self.assertIn("openai-whisper", stt_dockerfile)
        self.assertIn("ffmpeg", stt_dockerfile)





    def test_gateway_http_does_not_import_or_call_hermes_kanban_dispatcher(self) -> None:
        gateway_tree = ast.parse((REPO_ROOT / "services/gateway_http/app.py").read_text(encoding="utf-8"))
        forbidden_imports: list[str] = []
        forbidden_calls: list[str] = []
        for node in ast.walk(gateway_tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                names = [alias.name for alias in getattr(node, "names", [])]
                imported = " ".join([module, *names])
                if "kanban" in imported or "hermes_cli" in imported:
                    forbidden_imports.append(imported)
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in {"dispatch_once", "dispatch_loop", "dispatch_ready", "kanban_command"}:
                    forbidden_calls.append(name)

        self.assertEqual(forbidden_imports, [])
        self.assertEqual(forbidden_calls, [])

    def test_compose_wires_internal_stt_service_to_tool_execution_surfaces(self) -> None:
        services = self._compose()["services"]
        self.assertIn("stt-http", services)
        self.assertNotIn("ports", services["stt-http"])
        self.assertEqual(services["stt-http"]["expose"], ["8765"])
        self.assertIn("reviews_data:/data/reviews:ro", services["stt-http"]["volumes"])
        self.assertIn("./.hermes/frank_execution:/hub/.hermes/frank_execution:ro", services["stt-http"]["volumes"])
        self.assertEqual(
            services["stt-http"]["environment"]["STT_ALLOWED_AUDIO_ROOTS"],
            "/data/reviews:/hub/.hermes/frank_execution",
        )

        for service_name in ("frank", "hermes-worker-queue", "tool-sandbox"):
            self.assertEqual(services[service_name]["environment"]["STT_HTTP_URL"], "http://stt-http:8765")


if __name__ == "__main__":
    unittest.main()
