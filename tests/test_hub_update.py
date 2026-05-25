import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import hub_update


class HubUpdatePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("initial\n")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.repo, check=True, capture_output=True)
        self.head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()

    def test_plan_with_missing_state_reports_bootstrap_without_side_effects(self):
        state_path = self.repo / "missing-state.json"

        plan = hub_update.build_plan(
            repo_dir=self.repo,
            target_ref="HEAD",
            profile="local-dev",
            state_path=state_path,
        )

        self.assertEqual(plan["action"], "plan")
        self.assertEqual(plan["profile"], "local-dev")
        self.assertEqual(plan["target"]["resolved_ref"], self.head)
        self.assertIsNone(plan["current"])
        self.assertIn("bootstrap_operator_state", plan["domains"])
        self.assertFalse(state_path.exists())

    def test_plan_with_existing_state_reports_current_to_target_ref(self):
        state_path = self.repo / "operator-state.json"
        state_path.write_text(json.dumps({
            "schema_version": 1,
            "profile": "local-dev",
            "source": {"ref": "old-ref"},
            "images": {"gateway_http": "old-image"},
        }))

        plan = hub_update.build_plan(
            repo_dir=self.repo,
            target_ref="HEAD",
            profile="local-dev",
            state_path=state_path,
        )

        self.assertEqual(plan["current"]["source_ref"], "old-ref")
        self.assertEqual(plan["target"]["requested_ref"], "HEAD")
        self.assertEqual(plan["target"]["resolved_ref"], self.head)
        self.assertIn("source_checkout", plan["domains"])
        self.assertIn("smoke", plan["domains"])

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown profile"):
            hub_update.build_plan(
                repo_dir=self.repo,
                target_ref="HEAD",
                profile="continuous-prod",
                state_path=self.repo / "state.json",
            )

    def test_unknown_ref_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "could not resolve ref"):
            hub_update.build_plan(
                repo_dir=self.repo,
                target_ref="not-a-real-ref",
                profile="local-dev",
                state_path=self.repo / "state.json",
            )

    def test_cli_plan_outputs_json(self):
        state_path = self.repo / "state.json"

        result = subprocess.run(
            [
                "python3",
                str(Path(hub_update.__file__).resolve()),
                "plan",
                "--repo-dir",
                str(self.repo),
                "--ref",
                "HEAD",
                "--profile",
                "local-dev",
                "--state",
                str(state_path),
            ],
            text=True,
            capture_output=True,
            check=True,
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["action"], "plan")
        self.assertEqual(payload["target"]["resolved_ref"], self.head)
        self.assertFalse(state_path.exists())

    def test_cli_apply_dry_run_outputs_plan_without_writing_state(self):
        state_path = self.repo / "state.json"

        result = subprocess.run(
            [
                "python3",
                str(Path(hub_update.__file__).resolve()),
                "apply",
                "--dry-run",
                "--repo-dir",
                str(self.repo),
                "--ref",
                "HEAD",
                "--profile",
                "local-dev",
                "--state",
                str(state_path),
            ],
            text=True,
            capture_output=True,
            check=True,
        )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["action"], "apply")
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["side_effects"])
        self.assertEqual(payload["plan"]["target"]["resolved_ref"], self.head)
        self.assertFalse(state_path.exists())

    def test_cli_apply_without_confirm_is_rejected(self):
        result = subprocess.run(
            [
                "python3",
                str(Path(hub_update.__file__).resolve()),
                "apply",
                "--repo-dir",
                str(self.repo),
                "--ref",
                "HEAD",
                "--profile",
                "local-dev",
                "--state",
                str(self.repo / "state.json"),
            ],
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("apply requires --confirm", result.stderr)

    def test_cloud_prod_apply_is_disabled_until_backend_check_exists(self):
        result = subprocess.run(
            [
                "python3",
                str(Path(hub_update.__file__).resolve()),
                "apply",
                "--confirm",
                "--repo-dir",
                str(self.repo),
                "--ref",
                "HEAD",
                "--profile",
                "cloud-prod",
                "--state",
                str(self.repo / "state.json"),
            ],
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("cloud-prod apply is disabled", result.stderr)


if __name__ == "__main__":
    unittest.main()
