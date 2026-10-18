"""Inert offline upgrade transactions; actual package/native qualification is separate."""
import copy,importlib.util,json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
S=Path(__file__).resolve().parents[1]/'scripts/process_mac.py'
spec=importlib.util.spec_from_file_location('workspace_upgrade',S);mac=importlib.util.module_from_spec(spec);spec.loader.exec_module(mac)
class UpgradeTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.base=Path(self.t.name).resolve()
  self.root=self.base/'Process-Workspace';self.root.mkdir(mode=0o700)
  for n in ['config','data','logs','authority','wheels','native','venv','venv/bin']:(self.root/n).mkdir(mode=0o700)
  rows=[]
  for name in ['frank-executor','process-runtime','service-management',*[f'dep-{i}' for i in range(12)]]:
   filename=name.replace('-','_')+'-0.1-py3-none-any.whl';raw=('old '+name).encode();(self.root/'wheels'/filename).write_bytes(raw)
   rows.append({'name':name,'filename':filename,'version':'0.1','bytes':len(raw),'sha256':mac.sha(self.root/'wheels'/filename)})
  native=[]
  for role,name in mac.WORKSPACE_ROLES.items():
   raw=('old '+role).encode();path=self.root/'native'/name;path.write_bytes(raw);path.chmod(0o500)
   native.append({'role':role,'filename':name,'bytes':len(raw),'sha256':mac.sha(path),'source_commit':'1'*40,'mode':0o500})
  self.old={'schema':'hub.process-macos-release.v2','platform':'darwin','machine':'arm64','python':[3,12],'native_profile':'workspace-v1','native':native,'wheels':rows,'components':{k:{'package':v,'source_commit':'1'*40} for k,v in [('frank','frank-executor'),('process-runtime','process-runtime'),('service-management','service-management')]}}
  mac.save(self.root/'profile.json',self.old);(self.root/'process_mac.py').write_bytes(b'old controller');(self.root/'control.py').write_bytes(b'old controller');mac.save(self.root/'installed-files.json',{})
  files={str(p.relative_to(self.root)):mac.sha(p) for p in [self.root/'profile.json',self.root/'process_mac.py',*list((self.root/'wheels').iterdir()),*list((self.root/'native').iterdir())]}
  mac.save(self.root/'bundle.json',{'schema':'hub.process-macos-bundle.v1','files':files})
  self.previous={'schema':'hub.process-macos-installed.v1','root':str(self.root),'bundle_sha256':mac.sha(self.root/'bundle.json'),'control_sha256':mac.sha(self.root/'control.py'),'installed_files_sha256':mac.sha(self.root/'installed-files.json'),'python_sha256':'a'*64,'authority_qualified':False};mac.save(self.root/'installed.json',self.previous)
  (self.root/'venv/bin/python').write_bytes(b'old interpreter fixture')
  for name in ['frank-workspace.json','workspace-admission.json','local.json','frank.json']:mac.save(self.root/'config'/name,{'preserve':name})
  mac.save(self.root/'authority/COMPLETE.json',{'preserve':'identity receipt'});(self.root/'authority/opaque-custody').write_bytes(b'opaque fixture never parsed');(self.root/'data/sentinel.db').write_bytes(b'business data fixture')
  self.new=copy.deepcopy(self.old);self.new['components']['frank']['source_commit']='2'*40
  self.bundle=self.base/'new-bundle';self.bundle.mkdir(mode=0o700);(self.bundle/'wheels').mkdir(mode=0o700);(self.bundle/'native').mkdir(mode=0o700)
  for row in self.new['wheels']:
   raw=(b'new Frank' if row['name']=='frank-executor' else (self.root/'wheels'/row['filename']).read_bytes());path=self.bundle/'wheels'/row['filename'];path.write_bytes(raw);row.update(bytes=len(raw),sha256=mac.sha(path))
  for row in self.new['native']:
   raw=(b'new owner setup' if row['role']=='owner-setup' else (self.root/'native'/row['filename']).read_bytes());path=self.bundle/'native'/row['filename'];path.write_bytes(raw);path.chmod(0o500);row.update(bytes=len(raw),sha256=mac.sha(path));row['source_commit']='2'*40 if row['role']=='owner-setup' else row['source_commit']
  mac.save(self.bundle/'profile.json',self.new);(self.bundle/'process_mac.py').write_bytes(S.read_bytes())
  files={str(p.relative_to(self.bundle)):mac.sha(p) for p in self.bundle.rglob('*') if p.is_file()};mac.save(self.bundle/'bundle.json',{'schema':'hub.process-macos-bundle.v1','files':files});self.pin=mac.sha(self.bundle/'bundle.json')
 def test_policy_allows_only_owner_frank_controller_upgrade(self):
  mac.workspace_upgrade_policy(self.old,self.new)
 def test_runtime_service_dependencies_and_native_provenance_cannot_change(self):
  variants=[]
  for name in ['process-runtime','service-management','dep-0']:
   value=copy.deepcopy(self.new);next(r for r in value['wheels'] if r['name']==name)['sha256']='b'*64;variants.append(value)
  for role,field in [('wallet-command','sha256'),('wallet-command','source_commit'),('context-verifier','source_commit')]:
   value=copy.deepcopy(self.new);next(r for r in value['native'] if r['role']==role)[field]='b'*(40 if field=='source_commit' else 64);variants.append(value)
  value=copy.deepcopy(self.new);value['components']['process-runtime']['source_commit']='2'*40;variants.append(value)
  for value in variants:
   with self.assertRaises(mac.InstallError):mac.workspace_upgrade_policy(self.old,value)
 def contexts(self,fail=False):
  def installed(root,**kwargs):
   if kwargs.get('own_upgrade'):
    self.assertEqual(mac.load(root/'installed.json')['bundle_sha256'],self.pin);mac.check_bundle(root,self.pin);return mac.load(root/'installed.json')
   mac.check_bundle(root,self.previous['bundle_sha256']);return self.previous
  def create(path):
   if fail:raise mac.InstallError('inert offline install failure')
   (path/'bin').mkdir(mode=0o700,parents=True);(path/'bin/python').write_bytes(b'new interpreter fixture')
  def run(argv,root,**kwargs):
   if '_seal' in argv:mac.save(root/'installed-files.json',{})
   return subprocess.CompletedProcess(argv,0,b'{}',b'')
  return [patch.object(mac,'host'),patch.object(mac,'installed',side_effect=installed),patch.object(mac,'workspace_profile',return_value=self.old),patch.object(mac,'workspace_stopped'),patch.object(mac,'workspace_ready',return_value={'same':'receipt'}),patch.object(mac,'workspace_complete',return_value={'same':'receipt'}),patch.object(mac.venv.EnvBuilder,'create',side_effect=create),patch.object(mac,'run',side_effect=run)]
 def invoke(self,fail=False):
  from contextlib import ExitStack
  with ExitStack() as stack:
   for context in self.contexts(fail):stack.enter_context(context)
   return mac.workspace_software_upgrade(self.bundle,self.root,self.pin)
 def test_upgrade_preserves_data_authority_configs_and_old_release(self):
  original=(self.root/'installed.json').read_bytes();data=(self.root/'data/sentinel.db').read_bytes();authority=(self.root/'authority/opaque-custody').read_bytes();configs={p.name:p.read_bytes() for p in (self.root/'config').iterdir()}
  receipt=self.invoke();self.assertTrue(receipt['data_preserved']);self.assertTrue(receipt['authority_preserved']);self.assertFalse((self.root/mac.WORKSPACE_UPGRADE_HOLD).exists())
  previous=next((self.root/'software-upgrades').iterdir())/'previous'
  self.assertEqual((previous/'installed.json').read_bytes(),original);self.assertEqual((previous/'venv/bin/python').read_bytes(),b'old interpreter fixture')
  self.assertEqual((self.root/'data/sentinel.db').read_bytes(),data);self.assertEqual((self.root/'authority/opaque-custody').read_bytes(),authority);self.assertEqual({p.name:p.read_bytes() for p in (self.root/'config').iterdir()},configs)
  self.assertEqual(mac.load(self.root/'installed.json')['bundle_sha256'],self.pin);self.assertEqual(mac.load(previous/'profile.json'),self.old)
 def test_interrupted_upgrade_retains_hold_backups_and_blocks_installed(self):
  with self.assertRaisesRegex(mac.InstallError,'offline install failure'):self.invoke(fail=True)
  self.assertTrue((self.root/mac.WORKSPACE_UPGRADE_HOLD).exists());previous=next((self.root/'software-upgrades').iterdir())/'previous';self.assertEqual(mac.load(previous/'installed.json'),self.previous)
  with self.assertRaisesRegex(mac.InstallError,'requires reconciliation'):mac.installed(self.root)
  with self.assertRaisesRegex(mac.InstallError,'requires reconciliation'):mac.workspace_software_upgrade(self.bundle,self.root,self.pin)
 def test_wrong_bundle_pin_changes_no_installation_object(self):
  original=(self.root/'installed.json').read_bytes()
  with self.assertRaises(mac.InstallError):mac.workspace_software_upgrade(self.bundle,self.root,'f'*64)
  self.assertEqual((self.root/'installed.json').read_bytes(),original);self.assertFalse((self.root/'software-upgrades').exists())
if __name__=='__main__':unittest.main()
