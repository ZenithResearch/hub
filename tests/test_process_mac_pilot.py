"""Pilot actions use only selected private files; no keys or services are needed here."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/process_mac.py'
spec = importlib.util.spec_from_file_location('process_mac_pilot', SCRIPT)
mac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mac)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class PilotActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base/'Process-Pilot'
        self.root.mkdir(mode=0o700)
        for name in ('config', 'data', 'logs', 'native'):
            (self.root/name).mkdir(mode=0o700)
        native = [{'role': role, 'filename': filename, 'bytes': 1, 'sha256': '1'*64,
                   'source_commit': '2'*40, 'mode': 0o500} for role, filename in mac.NATIVE_ROLES.items()]
        mac.save(self.root/'profile.json', {'schema': 'hub.process-macos-release.v2',
            'platform': 'darwin', 'machine': 'arm64', 'python': [3,12], 'components': {},
            'wheels': [], 'native_profile': 'local-review-v1', 'native': native})
        mac.save(self.root/'config/local.json', {'schema': 'hub.process-macos-config.v1',
            'mode': 'local_review', 'runtime_port': 8192, 'service_port': 8191})
        mac.save(self.root/'config/frank.json', {'environment': 'production', 'allow_local_dev_untrusted': False,
            'integrations': {'schema': 'frank.integrations.v1', 'integrations': [], 'actors': []}})
        mac.save(self.root/'config/pilot-setup-plan.json', {'schema': 'castalia.wallet-local-review-setup.v1',
            'pilot_root': str(self.root), 'composition_sha256': '3'*64})
        mac.save(self.root/'config/pilot-workflow.json', {'fixture': 'owner-selected public metadata'})
        self.selection = {'schema': 'hub.process-macos-pilot.v1',
            'setup_plan_sha256': mac.sha(self.root/'config/pilot-setup-plan.json'),
            'workflow_sha256': mac.sha(self.root/'config/pilot-workflow.json')}
        mac.save(self.root/mac.PILOT_SELECTION, self.selection)
        self.pin = mac.sha(self.root/mac.PILOT_SELECTION)

    def complete(self):
        (self.root/'authority').mkdir(mode=0o700)
        (self.root/'authority/config').mkdir(mode=0o700)
        hashes = {}
        for name in ('command-authority.json', 'wallet-command.json', 'runtime-policy.json',
                     'runtime-receiver.json', 'result-authority.json', 'result-policy.json',
                     'frank-local-review.json'):
            mac.save(self.root/'authority/config'/name, {'fixture': name})
            hashes[name] = mac.sha(self.root/'authority/config'/name)
        value = {'schema': 'castalia.wallet-local-review-setup-complete.v1',
            'plan_sha256': self.selection['setup_plan_sha256'], 'root': str(self.root),
            'run_id': mac.PILOT_RUN, 'activation': False, 'configuration_hashes': hashes}
        mac.save(self.root/'authority/COMPLETE.json', value)
        return value

    def test_default_installation_mode_still_denies_pilot_actions(self):
        p = self.root/'config/local.json'; value = mac.load(p); value['mode'] = 'installation_only'
        p.write_text(json.dumps(value))
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot') as run:
            with self.assertRaisesRegex(mac.InstallError, 'isolated'):
                mac.pilot_action(self.root, 'pilot-setup', self.pin)
            run.assert_not_called()

    def test_setup_uses_exact_fixed_native_command_and_same_read_pin(self):
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot', return_value={'status': 'configured_not_activated'}) as run:
            value = mac.pilot_action(self.root, 'pilot-setup', self.pin)
        self.assertEqual(value['status'], 'configured_not_activated')
        argv, root, action = run.call_args.args
        self.assertEqual(argv, [self.root/'native/castalia-wallet-local-review-setup', 'create', '--plan',
            self.root/'config/pilot-setup-plan.json', '--plan-sha256', self.selection['setup_plan_sha256']])
        self.assertEqual((root, action), (self.root, 'pilot-setup'))

    def test_changed_selection_or_selected_input_denies_before_native_launch(self):
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot') as run:
            with self.assertRaises(mac.InstallError):
                mac.pilot_action(self.root, 'pilot-setup', '0'*64)
            (self.root/'config/pilot-workflow.json').write_text('{}')
            with self.assertRaises(mac.InstallError):
                mac.pilot_action(self.root, 'pilot-setup', self.pin)
            run.assert_not_called()

    def test_partial_authority_root_is_preserved_and_never_recreated(self):
        authority = self.root/'authority'; authority.mkdir(mode=0o700)
        (authority/'keep').write_bytes(b'partial')
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot') as run:
            with self.assertRaisesRegex(mac.InstallError, 'already exists'):
                mac.pilot_action(self.root, 'pilot-setup', self.pin)
            run.assert_not_called()
        self.assertEqual((authority/'keep').read_bytes(), b'partial')
        self.assertEqual(mac.pilot_report(self.root)['setup'], 'partial')

    def test_complete_receipt_binds_all_seven_configuration_outputs(self):
        receipt = self.complete()
        self.assertEqual(mac.pilot_complete(self.root, self.selection), receipt)
        self.assertEqual(mac.pilot_report(self.root)['setup'], 'configured')
        (self.root/'authority/config/wallet-command.json').write_text('{}')
        with self.assertRaisesRegex(mac.InstallError, 'configuration changed'):
            mac.pilot_complete(self.root, self.selection)

    def test_private_inputs_reject_symlink_hardlink_fifo_duplicate_and_extra_selection(self):
        selected = self.root/mac.PILOT_SELECTION
        original = selected.read_bytes()
        other = self.root/'config/other'; other.write_bytes(original); other.chmod(0o600)
        selected.unlink(); selected.symlink_to(other)
        with self.assertRaises(mac.InstallError): mac.pilot_inputs(self.root)
        selected.unlink(); os.link(other, selected)
        with self.assertRaises(mac.InstallError): mac.pilot_inputs(self.root)
        selected.unlink(); os.mkfifo(selected, 0o600)
        with self.assertRaises(mac.InstallError): mac.pilot_inputs(self.root)
        selected.unlink(); selected.write_bytes(b'{"schema":1,"schema":2}'); selected.chmod(0o600)
        with self.assertRaises(mac.InstallError): mac.pilot_inputs(self.root)
        selected.write_text(json.dumps({**self.selection, 'argv': ['sh']}))
        with self.assertRaises(mac.InstallError): mac.pilot_inputs(self.root)

    def test_persisted_operation_blocks_new_actions_without_signalling_saved_ids(self):
        marker = {'schema': 'hub.local-review.operation.v1', 'operation_id': 'a'*32,
                  'action': 'pilot-setup', 'state': 'cleanup_unresolved'}
        mac.save(self.root/mac.PILOT_OPERATION, marker)
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot') as run, patch.object(mac.os, 'kill') as kill:
            with self.assertRaisesRegex(mac.InstallError, 'cleanup'):
                mac.pilot_action(self.root, 'pilot-setup', self.pin)
            run.assert_not_called(); kill.assert_not_called()
        self.assertEqual(mac.load(self.root/mac.PILOT_OPERATION), marker)

    def test_only_matching_operation_may_be_removed(self):
        marker = {'schema': 'hub.local-review.operation.v1', 'operation_id': 'a'*32,
                  'action': 'pilot-setup', 'state': 'running'}
        mac.save(self.root/mac.PILOT_OPERATION, marker)
        with self.assertRaisesRegex(mac.InstallError, 'changed'):
            mac.finish_pilot_operation(self.root, {**marker, 'operation_id': 'b'*32})
        self.assertEqual(mac.load(self.root/mac.PILOT_OPERATION), marker)
        mac.finish_pilot_operation(self.root, marker, unresolved=True)
        self.assertEqual(mac.pilot_operation(self.root)['state'], 'cleanup_unresolved')

    def test_admission_uses_existing_cas_operator_and_preserves_mismatched_policy(self):
        def apply(argv, root, **unused):
            self.assertEqual(argv[:5], [root/'venv/bin/python', '-I', '-B', '-m', 'process_runtime.admission'])
            self.assertEqual(argv[5:8], ['apply', '--policy', root/'config/runtime-admission.json'])
            request = mac.load(argv[-1]); self.assertIsNone(request['expected_digest'])
            mac.save(root/'config/runtime-admission.json', request['policy'])
            return subprocess.CompletedProcess(argv, 0, b'{}', b'')
        with patch.object(mac, 'run', side_effect=apply) as run:
            mac.pilot_admission(self.root, self.selection)
            self.assertEqual(run.call_count, 1)
            mac.pilot_admission(self.root, self.selection)
            self.assertEqual(run.call_count, 1)
        p = self.root/'config/runtime-admission.json'; original = p.read_bytes()
        p.write_text(json.dumps({'schema': 'runtime.new-run-admission.v1', 'enabled': False, 'composition_digest': '9'*64}))
        wrong = p.read_bytes()
        with patch.object(mac, 'run') as run, self.assertRaisesRegex(mac.InstallError, 'differs'):
            mac.pilot_admission(self.root, self.selection)
        self.assertEqual(p.read_bytes(), wrong); self.assertNotEqual(wrong, original)
        run.assert_not_called()

    def test_workflow_is_absent_before_setup_and_mandatory_for_execution(self):
        p = self.root/mac.PILOT_SELECTION; value = mac.load(p); value['workflow_sha256'] = None
        p.write_text(json.dumps(value)); (self.root/'config/pilot-workflow.json').unlink()
        pin = mac.sha(p)
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot', return_value={}) as run:
            mac.pilot_action(self.root, 'pilot-setup', pin)
            self.assertEqual(run.call_count, 1)
            with self.assertRaisesRegex(mac.InstallError, 'workflow after native setup'):
                mac.pilot_action(self.root, 'pilot-next', pin)
            self.assertEqual(run.call_count, 1)

    def test_workflow_command_pins_exact_master_and_workflow_and_never_takes_argv(self):
        receipt = self.complete()
        argv = mac.pilot_cli(self.root, self.selection, receipt, 'next')
        self.assertEqual(argv[:6], [self.root/'venv/bin/python', '-I', '-B', '-m',
            'frank_executor.local_review_cli', 'next'])
        self.assertEqual(argv[6:], ['--config', self.root/'authority/config/frank-local-review.json',
            '--config-sha256', receipt['configuration_hashes']['frank-local-review.json'],
            '--workflow', self.root/'config/pilot-workflow.json', '--workflow-sha256', self.selection['workflow_sha256']])
        with self.assertRaises(mac.InstallError): mac.pilot_cli(self.root, self.selection, receipt, 'sh')

    def test_inspection_is_an_explicit_dated_observation_and_never_readiness(self):
        summary = {'schema': 'frank.local-review.inspection.v1', 'run_id': mac.PILOT_RUN,
            'run_state': 'completed', 'recoverable': False, 'export_available': True,
            'steps': [], 'pending_commands': [], 'unresolved_effects': []}
        with patch.object(mac.time, 'time', return_value=123):
            retained = mac.retain_pilot_inspection(self.root, summary)
        self.assertEqual(retained['observed_at'], 123000)
        self.assertNotIn('pilot_ready', retained)
        self.assertEqual(mac.load(self.root/'data/pilot-observation.json'), retained)
        with self.assertRaises(mac.InstallError):
            mac.retain_pilot_inspection(self.root, {**summary, 'run_id': 'other'})
        self.assertEqual(mac.load(self.root/'data/pilot-observation.json'), retained)

    def test_pilot_timeout_drains_with_sigint_before_forced_kill_and_quarantines(self):
        events = []
        class Child:
            returncode = None
            def __init__(self, argv, **kwargs): kwargs['stdout'].write(b'{}')
            def poll(self): return self.returncode
            def send_signal(self, signum): events.append(('signal', signum))
            def wait(self, timeout):
                events.append(('wait', timeout))
                if timeout == 15: raise subprocess.TimeoutExpired('owned', timeout)
                return self.returncode
            def kill(self): events.append(('kill',)); self.returncode = -9
        with patch.object(mac.subprocess, 'Popen', Child), patch.object(mac.time, 'monotonic', side_effect=[0,2]):
            with self.assertRaisesRegex(mac.InstallError, 'timed out'):
                mac.run_pilot(['fixed-cli'], self.root, 'pilot-next', timeout=1)
        self.assertEqual(events[:3], [('signal', mac.signal.SIGINT), ('wait', 15), ('kill',)])
        self.assertEqual(mac.pilot_operation(self.root)['state'], 'cleanup_unresolved')
        with patch.object(mac.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(mac.InstallError, 'cleanup'):
                mac.run_pilot(['fixed-cli'], self.root, 'pilot-next')
            popen.assert_not_called()

    def test_cleanup_exit_four_retains_hold_but_clean_unknown_exit_allows_exact_resume(self):
        class Child:
            code = 4
            def __init__(self, argv, **kwargs):
                self.returncode = self.code; kwargs['stdout'].write(b'{}')
            def poll(self): return self.returncode
        with patch.object(mac.subprocess, 'Popen', Child):
            with self.assertRaisesRegex(mac.InstallError, 'cleanup ownership'):
                mac.run_pilot(['fixed-cli'], self.root, 'pilot-next')
        held = mac.pilot_operation(self.root)
        self.assertEqual(held['state'], 'cleanup_unresolved')
        mac.finish_pilot_operation(self.root, held)
        Child.code = 3
        with patch.object(mac.subprocess, 'Popen', Child):
            with self.assertRaisesRegex(mac.InstallError, 'outcome unknown'):
                mac.run_pilot(['fixed-cli'], self.root, 'pilot-next')
        self.assertIsNone(mac.pilot_operation(self.root))

    def test_cli_owned_cleanup_marker_blocks_even_a_new_hub_process(self):
        mac.save(self.root/'data/frank-local-review-cleanup.json', {'invalid': 'retained'})
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot') as run:
            with self.assertRaisesRegex(mac.InstallError, 'cleanup'):
                mac.pilot_action(self.root, 'pilot-setup', self.pin)
            run.assert_not_called()
        self.assertTrue(mac.pilot_report(self.root)['cleanup_unresolved'])


    def test_uncontracted_or_signalled_exit_keeps_persistent_cleanup_hold(self):
        # Process termination alone says nothing about detached helper cleanup.
        for code in (-9, -2, 1, 130):
            with self.subTest(code=code):
                class Child:
                    def __init__(self, argv, **kwargs):
                        self.returncode = code; kwargs['stdout'].write(b'{}')
                    def poll(self): return self.returncode
                with patch.object(mac.subprocess, 'Popen', Child):
                    with self.assertRaises(mac.InstallError):
                        mac.run_pilot(['fixed-cli'], self.root, 'pilot-next')
                held = mac.pilot_operation(self.root)
                self.assertIsNotNone(held)
                self.assertEqual(held['state'], 'cleanup_unresolved')
                with patch.object(mac.subprocess, 'Popen') as popen:
                    with self.assertRaisesRegex(mac.InstallError, 'cleanup'):
                        mac.run_pilot(['fixed-cli'], self.root, 'pilot-resume')
                    popen.assert_not_called()
                # Test isolation only; production exposes no clear-hold action.
                mac.finish_pilot_operation(self.root, held)

    def test_only_explicit_clean_contract_exit_codes_remove_matching_operation(self):
        for code in (0, 2, 3):
            with self.subTest(code=code):
                class Child:
                    def __init__(self, argv, **kwargs):
                        self.returncode = code; kwargs['stdout'].write(b'{}')
                    def poll(self): return self.returncode
                with patch.object(mac.subprocess, 'Popen', Child):
                    if code:
                        with self.assertRaisesRegex(mac.InstallError, 'outcome unknown'):
                            mac.run_pilot(['fixed-cli'], self.root, 'pilot-next')
                    else:
                        self.assertEqual(mac.run_pilot(['fixed-cli'], self.root, 'pilot-next'), {})
                self.assertIsNone(mac.pilot_operation(self.root))


    def test_legacy_start_without_mode_remains_default_deny_and_checks_ports(self):
        # Existing callers/tests may supply only the old two-port config view.
        # Missing mode must never select native pilot setup or authority.
        old = {'runtime_port': 8092, 'service_port': 8091}
        with patch.object(mac, 'installed'), patch.object(mac, 'config', return_value=old), \
                patch.object(mac, 'launch_status', return_value=False), \
                patch.object(mac, 'check_ports', side_effect=mac.InstallError('occupied legacy port')) as ports, \
                patch.object(mac, 'pilot_inputs') as pilot, patch.object(mac, 'run') as run:
            with self.assertRaisesRegex(mac.InstallError, 'occupied legacy port'):
                mac.start(self.root)
            ports.assert_called_once_with(old)
            pilot.assert_not_called(); run.assert_not_called()


    def test_automatic_inspection_has_inner_budget_and_outer_cleanup_headroom(self):
        self.complete()
        summary = {'schema': 'frank.local-review.inspection.v1', 'run_id': mac.PILOT_RUN,
            'run_state': 'running', 'recoverable': False, 'export_available': False,
            'steps': [], 'pending_commands': [], 'unresolved_effects': []}
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot', side_effect=[{'state': 'observed'}, summary]) as run:
            result = mac.pilot_action(self.root, 'pilot-next', self.pin)
        self.assertEqual(result, {'action': 'pilot-next', 'action_completed': True, 'inspection': 'retained'})
        action, inspect = run.call_args_list
        self.assertEqual(action.kwargs['timeout'], 90)
        self.assertNotIn('--wait-seconds', action.args[0])
        self.assertEqual(inspect.args[0][-2:], ['--wait-seconds', '15'])
        self.assertEqual(inspect.args[1:], (self.root, 'pilot-inspect'))
        self.assertEqual(inspect.kwargs['timeout'], 30)
        self.assertEqual(mac.load(self.root/'data/pilot-observation.json')['run_state'], 'running')
        # Explicit inspection keeps the existing CLI default60/wrapper90 path.
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot', return_value=summary) as run:
            mac.pilot_action(self.root, 'pilot-inspect', self.pin)
        self.assertNotIn('--wait-seconds', run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['timeout'], 90)

    def test_unavailable_autoinspection_preserves_successful_mutation_without_fake_observation(self):
        self.complete()
        with patch.object(mac, 'installed'), patch.object(mac, 'run_pilot',
                side_effect=[{'state': 'observed'}, mac.InstallError('inspection unavailable')]) as run:
            result = mac.pilot_action(self.root, 'pilot-next', self.pin)
        self.assertEqual(result, {'action': 'pilot-next', 'action_completed': True, 'inspection': 'unavailable'})
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][-2:], ['--wait-seconds', '15'])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertFalse((self.root/'data/pilot-observation.json').exists())


if __name__ == '__main__': unittest.main()
