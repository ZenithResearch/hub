import importlib.util,unittest,tempfile,json,hashlib,os
from pathlib import Path
from unittest.mock import patch
S=Path(__file__).resolve().parents[1]/'scripts/process_mac.py'
spec=importlib.util.spec_from_file_location('mac_workspace',S);mac=importlib.util.module_from_spec(spec);spec.loader.exec_module(mac)
class WorkspaceTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
  self.root=Path(self.t.name).resolve()/'Process-Workspace';self.root.mkdir(mode=0o700)
  for n in ['config','data','logs','authority','authority/config']: (self.root/n).mkdir(mode=0o700)
  self.profile={'schema':'hub.process-macos-release.v2','platform':'darwin','machine':'arm64','python':[3,12],'components':{},'wheels':[], 'native_profile':'workspace-v1','native':[{'role':k,'filename':v,'bytes':1,'sha256':'a'*64,'source_commit':'b'*40,'mode':0o500} for k,v in mac.WORKSPACE_ROLES.items()]}
  mac.save(self.root/'profile.json',self.profile)
  mac.save(self.root/'config/local.json',{'schema':'hub.process-macos-config.v1','mode':'workspace','runtime_port':8292,'service_port':8291})
  mac.save(self.root/'config/frank.json',{'environment':'production','allow_local_dev_untrusted':False,'integrations':{'schema':'frank.integrations.v1','integrations':[],'actors':[]}})
  mac.save(self.root/'config/workspace-setup-plan.json',{'schema':'castalia.wallet-workspace-setup.v1','workspace_root':str(self.root)})
  hashes={}
  for n in mac.WORKSPACE_CONFIGS:
   mac.save(self.root/'authority/config'/n,{'fixture':n});hashes[n]=mac.sha(self.root/'authority/config'/n)
  self.receipt={'schema':'castalia.wallet-workspace-setup-complete.v1','root':str(self.root),'activation':False,'plan_sha256':mac.sha(self.root/'config/workspace-setup-plan.json'),'configuration_hashes':hashes}
  mac.save(self.root/'authority/COMPLETE.json',self.receipt)
  mac.save(self.root/'config/frank-workspace.json',mac.workspace_master(self.root,self.receipt))
  mac.save(self.root/'config/workspace-admission.json',{'schema':'runtime.new-run-admission.v1','enabled':False,'composition_digest':self.receipt['plan_sha256']})
 def test_release_has_no_result_authority(self):
  self.assertEqual(len(mac.native_rows(self.profile)),3)
  self.profile['native'].append({'role':'result-attester'})
  with self.assertRaises(mac.InstallError):mac.native_rows(self.profile)
 def test_empty_setup_does_not_require_workflow(self):
  self.assertEqual(mac.workspace_complete(self.root),self.receipt)
  report=mac.workspace_report(self.root)
  self.assertEqual(report['setup'],'configured');self.assertFalse(report['workflow_selected']);self.assertFalse(report['execution_enabled'])
  self.assertFalse((self.root/'data/runtime.db').exists())
 def test_native_configuration_tamper_blocks_start(self):
  (self.root/'authority/config/wallet-command.json').write_text('{}')
  with patch.object(mac,'installed'),patch.object(mac,'launch_status',return_value=False),patch.object(mac,'check_ports') as ports:
   with self.assertRaisesRegex(mac.InstallError,'configuration changed'):mac.start(self.root)
   ports.assert_not_called()
 def test_other_target_not_adopted(self):
  value=mac.load(self.root/'config/local.json');value['runtime_port']=8192;(self.root/'config/local.json').write_text(json.dumps(value))
  with self.assertRaisesRegex(mac.InstallError,'target differs'):mac.config(self.root)
 def test_existing_custody_is_not_reset(self):
  with patch.object(mac,'installed'),patch.object(mac,'workspace_run') as run:
   with self.assertRaisesRegex(mac.InstallError,'create-only'):mac.workspace_action(self.root,'workspace-setup')
   run.assert_not_called()
 def test_unreviewed_setup_plan_never_runs_native(self):
  import shutil
  shutil.rmtree(self.root/'authority')
  with patch.object(mac,'installed'),patch.object(mac,'launch_status',return_value=False),patch.object(mac,'workspace_run') as run:
   with self.assertRaisesRegex(mac.InstallError,'changed after review'):mac.workspace_action(self.root,'workspace-setup',selection='f'*64)
   run.assert_not_called()
 def request(self,action='catalog'):
  q={'schema':'zenith.workspace-command.v1','action':action,'payload':{},'idempotency_key':'fixed-operation-0001'}
  raw=json.dumps(q);return raw,hashlib.sha256(raw.encode()).hexdigest()
 def test_same_identity_changed_action_is_rejected(self):
  with patch.object(mac,'installed'),patch.object(mac,'owns_listener',return_value=True),patch.object(mac,'workspace_run',return_value={'ok':True}) as run:
   mac.workspace_action(self.root,'workspace-command',*self.request())
   with self.assertRaisesRegex(mac.InstallError,'changed action'):mac.workspace_action(self.root,'workspace-command',*self.request('get'))
   self.assertEqual(run.call_count,1)
 def test_symlink_input_is_never_followed(self):
  requests=self.root/'data/workspace-requests';requests.mkdir(mode=0o700)
  target=self.root/'data/other';target.write_text('{}')
  (requests/'fixed-operation-0001.json').symlink_to(target)
  with patch.object(mac,'installed'),patch.object(mac,'owns_listener',return_value=True),patch.object(mac,'workspace_run') as run:
   with self.assertRaises(mac.InstallError):mac.workspace_action(self.root,'workspace-command',*self.request())
   run.assert_not_called()
 def test_dispatch_pins_the_same_validated_master_read(self):
  master=self.root/'config/frank-workspace.json';accepted=master.read_bytes();original=mac.bounded_private;reads=0
  def substitute(path, maximum=65536):
   nonlocal reads
   raw=original(path,maximum)
   if path==master:
    reads+=1
    if reads==2: master.write_text('{}')
   return raw
  with patch.object(mac,'installed'),patch.object(mac,'owns_listener',return_value=True),patch.object(mac,'bounded_private',side_effect=substitute),patch.object(mac,'workspace_run',return_value={}) as run:
   mac.workspace_action(self.root,'workspace-command',*self.request())
   args=run.call_args.args[0]
   self.assertEqual(args[args.index('--config-sha256')+1],hashlib.sha256(accepted).hexdigest())
   self.assertNotEqual(args[args.index('--config-sha256')+1],mac.sha(master))
 def test_setup_never_repins_a_changed_owner_plan(self):
  import shutil
  shutil.rmtree(self.root/'authority');plan=self.root/'config/workspace-setup-plan.json';accepted=mac.sha(plan);original=mac.bounded_private
  def substitute(path, maximum=65536):
   raw=original(path,maximum)
   if path==plan: plan.write_text('{"changed":true}')
   return raw
  with patch.object(mac,'installed'),patch.object(mac,'launch_status',return_value=False),patch.object(mac,'bounded_private',side_effect=substitute),patch.object(mac,'workspace_run',side_effect=mac.InstallError('stop before native')) as run:
   with self.assertRaisesRegex(mac.InstallError,'stop before native'):mac.workspace_action(self.root,'workspace-setup',selection=accepted)
   args=run.call_args.args[0]
   self.assertEqual(args[args.index('--plan-sha256')+1],accepted)
   self.assertNotEqual(accepted,mac.sha(plan))
 def test_proposal_refresh_never_creates_custody_and_preserves_prior_plan(self):
  import shutil
  shutil.rmtree(self.root/'authority');plan=self.root/'config/workspace-setup-plan.json';before=plan.read_bytes();pin=mac.sha(plan)
  with patch.object(mac.time,'time',return_value=100000):
   result=mac.workspace_prepare_plan(self.root,pin)
  value=mac.load(plan)
  self.assertEqual((int(value['not_before']),int(value['not_after'])),(99995,103590))
  self.assertEqual(value['wallet_command_sha256'],'a'*64)
  self.assertFalse(result['authority_created']);self.assertFalse((self.root/'authority').exists())
  self.assertEqual((self.root/'config'/('workspace-setup-plan-history-'+pin+'.json')).read_bytes(),before)
 def test_proposal_cas_rejects_stale_selection(self):
  import shutil
  shutil.rmtree(self.root/'authority');plan=self.root/'config/workspace-setup-plan.json';before=plan.read_bytes()
  with self.assertRaisesRegex(mac.InstallError,'changed after review'):mac.workspace_prepare_plan(self.root,'f'*64)
  self.assertEqual(plan.read_bytes(),before)
 def test_unexpected_child_exit_retains_cleanup_hold(self):
  with self.assertRaises(mac.InstallError):mac.workspace_run(['/usr/bin/false'],self.root)
  self.assertTrue((self.root/'logs/workspace-operation.json').exists())
  with self.assertRaisesRegex(mac.InstallError,'cleanup is unresolved'):mac.workspace_run(['/usr/bin/true'],self.root)
 def test_known_denial_preserves_logs_but_releases_owned_hold(self):
  with self.assertRaises(mac.InstallError):mac.workspace_run(['/bin/sh','-c','exit 2'],self.root)
  self.assertFalse((self.root/'logs/workspace-operation.json').exists())
  self.assertEqual(len(list((self.root/'logs').glob('workspace-*.log'))),2)
if __name__=='__main__':unittest.main()
