"""Actual contained legacy output capture and replay through the typed runtime."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest

HUB = Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.environ.get('PROCESS_RUNTIME_REPO'), 'set PROCESS_RUNTIME_REPO for the contained legacy comparison')
class LegacyProcessShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0,str(HUB/'scripts'))
        spec=importlib.util.spec_from_file_location('legacy_process_verification',HUB/'scripts/verify_legacy_process_shadow.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cls.module=module
        cls.runtime_repo=Path(os.environ['PROCESS_RUNTIME_REPO'])
        cls.report=module.verify(cls.runtime_repo)

    def test_actual_legacy_success_and_degraded_failure_have_classified_migration(self):
        self.assertEqual(self.report['status'],'passed')
        self.assertFalse(self.report['production_ready'])
        self.assertEqual(self.report['comparison_mode'],'actual-contained-legacy-to-new-runtime-output-replay')
        self.assertEqual(set(self.report['source_sha256']),{'hub','process-runtime'})
        success,failure=self.report['scenarios']
        self.assertEqual((success['legacy_state'],success['runtime_state']),('completed','succeeded'))
        self.assertEqual((failure['legacy_state'],failure['runtime_state']),('failed','failed'))
        for scenario in (success,failure):
            self.assertEqual(scenario['unclassified_divergences'],[])
            self.assertNotEqual(scenario['initial_packet']['content_hash'],scenario['final_packet']['content_hash'])
            self.assertIn('immutable-artifacts',{row['category'] for row in scenario['classified_differences']})
            self.assertEqual(scenario['runnable_batches'][1],['step_2','step_3'])
        self.assertEqual(success['step_comparisons'][-1]['step_id'],'step_9')
        self.assertEqual(failure['step_comparisons'][-1]['step_id'],'step_8')
        self.assertEqual(failure['step_comparisons'][-1]['legacy_terminal'],'FAILED')
        self.assertIsNone(failure['step_comparisons'][-1]['migrated_output_digest'])
        self.assertIn('step_8-raw-outputs',failure['retained_replay_fixture']['artifacts'])

    def test_failed_command_replaces_prior_success_evidence(self):
        import json
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'
            output.write_text('{"status":"passed"}')
            with patch.object(self.module,'verify',side_effect=AssertionError('fixture divergence')):
                status=self.module.main(['--runtime-repo',str(self.runtime_repo),'--output',str(output)])
            failed=json.loads(output.read_text())
        self.assertEqual(status,1)
        self.assertEqual(failed['status'],'failed')
        self.assertFalse(failed['production_ready'])
        self.assertEqual(failed['error_type'],'AssertionError')

    def test_retained_bytes_replay_after_legacy_temporary_store_is_deleted(self):
        import json
        from process_runtime.shadow import run_shadow
        draft=json.loads((self.runtime_repo/'resources/processes/personal-review-v1.json').read_text())
        for scenario in self.report['scenarios']:
            replay=run_shadow(draft,scenario['retained_replay_fixture']).value
            self.assertEqual(replay,scenario['runtime_report'])
            self.assertEqual(self.module.digest(replay),scenario['runtime_report_sha256'])

if __name__=='__main__':unittest.main()
