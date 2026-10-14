"""One isolated public-contract drill with installed artifact or explicit source inputs."""
import importlib.util
import os
from pathlib import Path
import unittest

HUB = Path(__file__).resolve().parents[1]
REQUIRED = ('PROCESS_RUNTIME_REPO','SERVICE_MANAGEMENT_REPO','FRANK_REPO')

@unittest.skipUnless(os.environ.get('PROCESS_ARTIFACT_MANIFEST') or all(os.environ.get(name) for name in REQUIRED), 'set PROCESS_ARTIFACT_MANIFEST or all three source paths for the isolated drill')
class ProcessExecutionIntegrationTests(unittest.TestCase):
    def test_public_contracts_recovery_effects_revision_and_rollback(self):
        spec=importlib.util.spec_from_file_location('process_execution_verification',HUB/'scripts/verify_process_execution.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        inputs={'artifact_manifest':os.environ['PROCESS_ARTIFACT_MANIFEST']} if os.environ.get('PROCESS_ARTIFACT_MANIFEST') else {'runtime_repo':os.environ['PROCESS_RUNTIME_REPO'],'service_repo':os.environ['SERVICE_MANAGEMENT_REPO'],'frank_repo':os.environ['FRANK_REPO']}
        report=module.verify(**inputs,config_path=HUB/'config/process-execution.components.json')
        self.assertEqual(report['status'],'passed')
        self.assertFalse(report['production_ready'])
        checks={item['name']:item for item in report['checks']}
        self.assertEqual(set(checks),{'component-pins','parallel-ready-claim-race-join','runtime-frank-restart-receipt','expired-lease-fence','unknown-effect-no-resend','duplicate-delivery-no-repeat','service-intake-plan-outbox-frank','case-revision-explicit-carry-forward','production-default-deny','rollback-new-admission-only'})
        self.assertEqual(set(checks['component-pins']['evidence']),{'hub','process-runtime','service-management','frank'})
        self.assertTrue(all(item['status']=='passed' for item in checks.values()))
        self.assertEqual(checks['unknown-effect-no-resend']['evidence']['send_count_for_effect'],1)
        self.assertEqual(checks['rollback-new-admission-only']['evidence']['additional_sends'],0)
        rollback=checks['rollback-new-admission-only']['evidence']
        self.assertEqual(rollback['admission_consumer'],'runtime.new-run-admission.v1')
        self.assertEqual(rollback['new_run_rejection'],'not_ready')
        self.assertEqual(rollback['existing_run_state'],'succeeded')
        self.assertEqual(rollback['paused_handoff_state'],'pending')
        self.assertEqual(rollback['resumed_handoff_state'],'started')
        self.assertTrue(report['production_no_go'])

if __name__=='__main__':unittest.main()
