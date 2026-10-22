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
 def test_permission_expiry_uses_authenticated_receipt_and_does_not_reset_custody(self):
  self.receipt.update(not_before=100,not_after=3700,identity_not_after=31536100)
  (self.root/'authority/COMPLETE.json').unlink();mac.save(self.root/'authority/COMPLETE.json',self.receipt)
  with patch.object(mac.time,'time',return_value=3700):
   report=mac.workspace_report(self.root)
  self.assertEqual(report['setup'],'configured')
  self.assertEqual(report['permission'],{'state':'expired','not_before':'100','not_after':'3700','identity_not_after':'31536100','observed_at':'3700'})
  self.assertTrue((self.root/'authority/COMPLETE.json').exists())
 def test_permission_window_boundaries_and_legacy_missing_identity_expiry(self):
  receipt={'not_before':100,'not_after':3700}
  for at,state in [(99,'not_yet_valid'),(100,'active'),(3699,'active'),(3700,'expired')]:
   with patch.object(mac.time,'time',return_value=at):
    value=mac.workspace_permission(receipt)
   self.assertEqual(value['state'],state);self.assertIsNone(value['identity_not_after'])
 def test_malformed_permission_metadata_never_reports_active(self):
  for receipt in [{},{'not_before':True,'not_after':3700},{'not_before':100,'not_after':'3700'},{'not_before':100,'not_after':100},{'not_before':100,'not_after':3701},{'not_before':100,'not_after':3700,'identity_not_after':3699},{'not_before':100,'not_after':3700,'identity_not_after':9007199254740992}]:
   with patch.object(mac.time,'time',return_value=200):
    self.assertEqual(mac.workspace_permission(receipt)['state'],'unknown')
 def renewable(self):
  holder={k:'fixed-'+k for k in ['schema','store_id','subject_id','purpose','application','key_id','public_key_hex','public_key_sha256']}
  holder.update(key_generation=1,state_revision=1,created_at=100,key_created_at=100,published_at=100,not_before=100,not_after=31536100,state='active',revoked_at=None)
  self.receipt.update(native_generation=1,not_before=100,not_after=3700,identity_not_after=31536100,holder=holder,command_issuer={'key_id':'same-issuer'},journal_capacity='local_review_pilot_512',scope='five fixed scopes')
  (self.root/'authority/COMPLETE.json').unlink();mac.save(self.root/'authority/COMPLETE.json',self.receipt)
  with patch.object(mac.time,'time',return_value=100000),patch.object(mac,'launch_status',return_value=False),patch.object(mac,'check_ports'):
   proposal=mac.workspace_prepare_renewal(self.root)
  return proposal['plan_sha256']
 def renewal_runner(self,journal='bound',fail_native=False,change_identity=False,create_journal=False):
  import copy
  calls=[]
  def runner(argv,root,**kwargs):
   action=argv[1] if argv[0]==root/'native/castalia-wallet-workspace-setup' else argv[5]
   calls.append(action)
   if action=='check-renewal':
    return {'schema':'frank.workspace-renewal-readiness.v1','configuration_sha256':argv[argv.index('--config-sha256')+1],'journal':journal,'ready':True,'pending_commands':0,'cleanup_retained':False}
   if action in {'renew','resume-renewal'}:
    folder=root/'authority/renewals/2';folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if fail_native and action=='renew':
     mac.workspace_store(folder/'INTENT.json',b'{}');raise mac.InstallError('native recovery_required')
    plan=mac.closed_json(mac.bounded_private(Path(argv[3])));new=copy.deepcopy(self.receipt)
    new.update(schema='castalia.wallet-workspace-renewal-complete.v1',native_generation=2,plan_sha256=argv[5],previous_complete=plan['previous_complete'],not_before=int(plan['not_before']),not_after=int(plan['not_after']))
    new['holder']['state_revision']=2
    if change_identity:new['holder']['key_id']='changed-key'
    configs=folder/'config';configs.mkdir(mode=0o700,exist_ok=True)
    for name in mac.WORKSPACE_CONFIGS:
     mac.workspace_store(configs/name,mac.workspace_bytes({'fixture':name,'generation':2}));new['configuration_hashes'][name]=mac.sha(configs/name)
    mac.workspace_store(folder/'COMPLETE.json',mac.workspace_bytes(new))
    if create_journal:(root/'data/frank-workspace.db').write_bytes(b'unexpected')
    return {'schema':'castalia.wallet-workspace-renewal-output.v1','status':'configured_not_activated','complete':{'path':str(folder/'COMPLETE.json'),'sha256':mac.sha(folder/'COMPLETE.json')}}
   if action=='migrate-binding':
    migration=mac.closed_json(mac.bounded_private(Path(argv[argv.index('--input')+1])))
    return {'schema':'frank.workspace-migration-result.v1','previous_configuration_sha256':migration['previous_configuration']['sha256'],'configuration_sha256':argv[argv.index('--config-sha256')+1],'migration_sha256':argv[argv.index('--input-sha256')+1],'previous_complete_sha256':migration['previous_complete']['sha256'],'renewed_complete_sha256':migration['renewed_complete']['sha256'],'commands_preserved':True,'duplicate':False}
   self.fail(action)
  return runner,calls
 def renew_with(self,pin,runner,resume=False):
  with patch.object(mac,'launch_status',return_value=False),patch.object(mac,'check_ports'),patch.object(mac,'workspace_run',side_effect=runner):
   return mac.workspace_renew(self.root,pin,resume=resume)
 def test_renewal_preserves_identity_old_files_and_bound_journal(self):
  pin=self.renewable();old=(self.root/'authority/COMPLETE.json').read_bytes();journal=self.root/'data/frank-workspace.db';journal.write_bytes(b'opaque observed command history')
  runner,calls=self.renewal_runner();result=self.renew_with(pin,runner)
  self.assertEqual(calls,['check-renewal','renew','migrate-binding']);self.assertTrue(result['identity_preserved']);self.assertTrue(result['commands_preserved'])
  self.assertEqual((self.root/'authority/COMPLETE.json').read_bytes(),old);self.assertEqual(journal.read_bytes(),b'opaque observed command history')
  self.assertFalse((self.root/mac.WORKSPACE_RENEW_HOLD).exists());self.assertEqual(mac.workspace_ready(self.root)['native_generation'],2)
  master=mac.load(self.root/'config/frank-workspace.json');self.assertIn('/renewals/2/config/wallet-command.json',master['wallet']['configuration'])
 def test_absent_journal_renewal_never_creates_or_migrates_one(self):
  pin=self.renewable();runner,calls=self.renewal_runner(journal='absent');result=self.renew_with(pin,runner)
  self.assertEqual(calls,['check-renewal','renew']);self.assertEqual(result['journal'],'absent');self.assertIsNone(result['migration_sha256']);self.assertFalse((self.root/'data/frank-workspace.db').exists())
 def test_journal_appearing_after_absent_readiness_retains_renewal_hold(self):
  pin=self.renewable();runner,calls=self.renewal_runner(journal='absent',create_journal=True)
  with self.assertRaisesRegex(mac.InstallError,'journal appeared'):self.renew_with(pin,runner)
  self.assertTrue((self.root/mac.WORKSPACE_RENEW_HOLD).exists());self.assertNotIn('migrate-binding',calls)
 def test_native_uncertainty_retains_hold_and_exact_plan_resume_can_invoke(self):
  pin=self.renewable();runner,calls=self.renewal_runner(fail_native=True)
  with self.assertRaisesRegex(mac.InstallError,'recovery_required'):self.renew_with(pin,runner)
  self.assertTrue((self.root/mac.WORKSPACE_RENEW_HOLD).exists());self.assertTrue(mac.workspace_report(self.root)['renewal']['resumable'])
  self.renew_with(pin,runner,resume=True)
  self.assertEqual(calls,['check-renewal','renew','check-renewal','resume-renewal','migrate-binding']);self.assertFalse((self.root/mac.WORKSPACE_RENEW_HOLD).exists())
 def test_known_native_preflight_denial_without_intent_allows_new_public_proposal(self):
  pin=self.renewable();runner,calls=self.renewal_runner()
  def deny(argv,root,**kwargs):
   if argv[0]==root/'native/castalia-wallet-workspace-setup':
    failure=mac.InstallError('known preflight denial');failure.native_preflight_denied=True;raise failure
   return runner(argv,root,**kwargs)
  with self.assertRaisesRegex(mac.InstallError,'preflight denial'):self.renew_with(pin,deny)
  self.assertFalse((self.root/mac.WORKSPACE_RENEW_HOLD).exists());self.assertFalse((self.root/'authority/renewals').exists())
  with patch.object(mac.time,'time',return_value=100100),patch.object(mac,'launch_status',return_value=False),patch.object(mac,'check_ports'):
   self.assertNotEqual(mac.workspace_prepare_renewal(self.root,pin)['plan_sha256'],pin)
 def test_native_preflight_two_never_releases_existing_successor_artifacts(self):
  pin=self.renewable();runner,_=self.renewal_runner()
  def deny(argv,root,**kwargs):
   if argv[0]==root/'native/castalia-wallet-workspace-setup':
    folder=root/'authority/renewals/2';folder.mkdir(mode=0o700,parents=True);mac.save(folder/'INTENT.json',{});mac.save(folder/'COMPLETE.json',{})
    failure=mac.InstallError('known preflight denial');failure.native_preflight_denied=True;raise failure
   return runner(argv,root,**kwargs)
  with self.assertRaises(mac.InstallError):self.renew_with(pin,deny)
  self.assertTrue((self.root/mac.WORKSPACE_RENEW_HOLD).exists());self.assertFalse((self.root/mac.WORKSPACE_SELECTION).exists())
 def test_changed_identity_never_publishes_successor_selection(self):
  pin=self.renewable();runner,_=self.renewal_runner(change_identity=True)
  with self.assertRaisesRegex(mac.InstallError,'changed identity'):self.renew_with(pin,runner)
  self.assertFalse((self.root/mac.WORKSPACE_SELECTION).exists());self.assertTrue((self.root/mac.WORKSPACE_RENEW_HOLD).exists())
 def test_partial_native_renewal_blocks_start_and_preparation(self):
  self.renewable();folder=self.root/'authority/renewals/2';folder.mkdir(mode=0o700,parents=True);mac.save(folder/'INTENT.json',{})
  self.assertTrue(mac.workspace_holds(self.root))
  with patch.object(mac,'launch_status',return_value=False),patch.object(mac,'check_ports'):
   with self.assertRaisesRegex(mac.InstallError,'reconciled'):mac.workspace_prepare_renewal(self.root)
  self.assertTrue((folder/'INTENT.json').exists())
 def test_only_explicit_reaped_native_four_releases_generic_ownership(self):
  native=self.root/'native';native.mkdir(mode=0o700);command=native/'castalia-wallet-workspace-setup';command.write_text('#!/bin/sh\nexit 4\n');command.chmod(0o500)
  pin='a'*64;mac.save(self.root/mac.WORKSPACE_RENEW_HOLD,{'schema':'hub.workspace-renewal-operation.v1','plan_sha256':pin})
  argv=[command,'renew','--plan',self.root/'config/plan.json','--plan-sha256',pin]
  with self.assertRaises(mac.InstallError):mac.workspace_run(argv,self.root,native_renewal=True)
  self.assertFalse((self.root/'logs/workspace-operation.json').exists());self.assertTrue((self.root/mac.WORKSPACE_RENEW_HOLD).exists())
  command.chmod(0o700);command.write_text("#!/bin/sh\nprintf '%s\\n' '{\"status\":\"recovery_required\"}'\n");command.chmod(0o500);argv[1]='resume-renewal'
  self.assertEqual(mac.workspace_run(argv,self.root,native_renewal=True),{'status':'recovery_required'})
 def test_generic_four_keeps_ownership_hold(self):
  with self.assertRaises(mac.InstallError):mac.workspace_run(['/bin/sh','-c','exit 4'],self.root)
  self.assertTrue((self.root/'logs/workspace-operation.json').exists())
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
  self.assertEqual(int(value['identity_not_after']),99995+365*86400)
  self.assertGreater(int(value['identity_not_after']),int(value['not_after']))
  self.assertFalse(result['authority_created']);self.assertFalse((self.root/'authority').exists())
  self.assertEqual((self.root/'config'/('workspace-setup-plan-history-'+pin+'.json')).read_bytes(),before)
 def test_proposal_cas_rejects_stale_selection(self):
  import shutil
  shutil.rmtree(self.root/'authority');plan=self.root/'config/workspace-setup-plan.json';before=plan.read_bytes()
  with self.assertRaisesRegex(mac.InstallError,'changed after review'):mac.workspace_prepare_plan(self.root,'f'*64)
  self.assertEqual(plan.read_bytes(),before)
 def test_setup_plan_symlink_and_fifo_are_rejected_before_read(self):
  plan=self.root/'config/workspace-setup-plan.json';plan.unlink();target=self.root/'config/untrusted';target.write_text('{}');plan.symlink_to(target)
  with self.assertRaises(mac.InstallError):mac.workspace_complete(self.root)
  plan.unlink();os.mkfifo(plan,0o600)
  with self.assertRaises(mac.InstallError):mac.workspace_complete(self.root)
 def test_unexpected_child_exit_retains_cleanup_hold(self):
  with self.assertRaises(mac.InstallError):mac.workspace_run(['/usr/bin/false'],self.root)
  self.assertTrue((self.root/'logs/workspace-operation.json').exists())
  with self.assertRaisesRegex(mac.InstallError,'cleanup is unresolved'):mac.workspace_run(['/usr/bin/true'],self.root)
 def test_known_denial_preserves_logs_but_releases_owned_hold(self):
  with self.assertRaises(mac.InstallError):mac.workspace_run(['/bin/sh','-c','exit 2'],self.root)
  self.assertFalse((self.root/'logs/workspace-operation.json').exists())
  self.assertEqual(len(list((self.root/'logs').glob('workspace-*.log'))),2)
if __name__=='__main__':unittest.main()
