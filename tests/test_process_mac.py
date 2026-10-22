"""Mac installer failures must not damage an old install or unrelated service."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/process_mac.py'
spec = importlib.util.spec_from_file_location('process_mac', SCRIPT)
mac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mac)


class MacSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.fake_repo = self.base/'repo'
        (self.fake_repo/'config').mkdir(parents=True)
        self.wheels = self.base/'wheels';self.wheels.mkdir()
        rows = []
        for i in range(15):
            filename = f'package_{i}-1.0-py3-none-any.whl'
            raw = f'fixture wheel {i}'.encode();(self.wheels/filename).write_bytes(raw)
            rows.append({'filename':filename,'name':f'package-{i}','version':'1.0',
                         'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        profile = {'schema':'hub.process-macos-release.v1','platform':'darwin','machine':'arm64',
                   'python':[3,12],'components':{},'wheels':rows}
        (self.fake_repo/'config/process-macos-release.v1.json').write_text(json.dumps(profile))
        self.root_patch = patch.object(mac,'ROOT',self.fake_repo);self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.bundle = self.base/'bundle'
        self.pin = mac.bundle(self.wheels,self.bundle)['bundle_sha256']

    def test_offline_bundle_has_exact_profile_and_wheel_bytes(self):
        self.assertEqual(len(mac.check_bundle(self.bundle,self.pin)['wheels']),15)
        self.assertEqual(mac.sha(self.bundle/'process_mac.py'),mac.sha(SCRIPT))
        self.assertEqual((self.bundle/'Install.command').stat().st_mode & 0o777,0o700)

    def test_tampered_or_missing_wheel_rejected_before_destination_creation(self):
        wheel=next((self.bundle/'wheels').iterdir());wheel.write_bytes(b'bad')
        target=self.base/'new install'
        with self.assertRaisesRegex(mac.InstallError,'content differs'):
            mac.install(self.bundle,target,self.pin)
        self.assertFalse(target.exists())

    def test_wrong_release_pin_rejected(self):
        with self.assertRaisesRegex(mac.InstallError,'digest differs'):
            mac.check_bundle(self.bundle,'0'*64)

    def test_symlink_wheel_and_untracked_profile_payload_rejected(self):
        wheel=next((self.bundle/'wheels').iterdir());raw=wheel.read_bytes()
        original=self.base/'original.whl';original.write_bytes(raw);wheel.unlink();wheel.symlink_to(original)
        with self.assertRaisesRegex(mac.InstallError,'content differs'):
            mac.check_bundle(self.bundle,self.pin)

    def test_existing_install_and_data_never_overwritten(self):
        target=self.base/'old install';target.mkdir(mode=0o700);(target/'keep.db').write_bytes(b'old')
        with patch.object(mac,'host'),self.assertRaisesRegex(mac.InstallError,'already exists'):
            mac.install(self.bundle,target,self.pin)
        self.assertEqual((target/'keep.db').read_bytes(),b'old')

    def test_symlink_and_shared_install_roots_rejected(self):
        target=self.base/'private';target.mkdir(mode=0o700)
        link=self.base/'link';link.symlink_to(target)
        with self.assertRaisesRegex(mac.InstallError,'symlinks'):mac.private(link)
        target.chmod(0o755)
        with self.assertRaisesRegex(mac.InstallError,'private'):mac.private(target)

    def test_wrong_platform_rejected_without_install_mutation(self):
        with patch.object(mac.sys,'platform','linux'),self.assertRaisesRegex(mac.InstallError,'Apple silicon'):
            mac.host(mac.load(self.bundle/'profile.json'))

    def test_plists_preserve_spaces_quotes_unicode_as_literal_argv(self):
        root=self.base/"Jess's Mac Ω"
        value=plistlib.loads(plistlib.dumps(mac.job(root,'runtime')))
        self.assertIn(str(root),value['ProgramArguments'])
        self.assertEqual(value['ProgramArguments'][0],str(root/'venv/bin/python'))
        self.assertFalse(value['KeepAlive'])
        self.assertNotIn('--local-dev-untrusted',value['ProgramArguments'])
        self.assertNotEqual(mac.label(root,'runtime'),mac.label(self.base/'other','runtime'))

    def test_occupied_port_does_not_start_or_stop_any_job(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            with patch.object(mac,'installed'),patch.object(mac,'config',return_value={'runtime_port':port,'service_port':port+1}),patch.object(mac,'launch_status',return_value=False),patch.object(mac,'run') as runner:
                with self.assertRaisesRegex(mac.InstallError,'occupied'):mac.start(self.base)
                runner.assert_not_called()
                self.assertEqual(listener.getsockname()[1],port)

    def test_foreign_job_cannot_be_adopted_or_stopped(self):
        result=subprocess.CompletedProcess([],0,b'program = /another/app\n',b'')
        with patch.object(mac,'run',return_value=result),self.assertRaisesRegex(mac.InstallError,'different job'):
            mac.launch_status(self.base,'runtime')

    def test_stop_addresses_only_its_own_exact_launchd_labels(self):
        active=set(mac.COMPONENTS)
        def run(argv,root,**kwargs):
            self.assertEqual(argv[:2],['/bin/launchctl','bootout'])
            name=next(n for n in active if argv[2].endswith(mac.label(root,n)))
            active.remove(name)
            return subprocess.CompletedProcess([],0,b'',b'')
        with patch.object(mac,'launch_status',side_effect=lambda root,name:name in active),patch.object(mac,'run',side_effect=run):
            self.assertTrue(mac.stop(self.base)['stopped'])
        self.assertFalse(active)

    def test_provider_or_local_dev_activation_is_not_an_install_setting(self):
        root=self.base/'install';(root/'config').mkdir(parents=True,mode=0o700)
        os.chmod(root,0o700)
        value={'schema':'hub.process-macos-config.v1','mode':'installation_only','runtime_port':8092,'service_port':8091}
        mac.save(root/'config/local.json',value)
        mac.save(root/'config/frank.json',{'environment':'production','allow_local_dev_untrusted':True,
                                         'integrations':{'integrations':[]}})
        with self.assertRaisesRegex(mac.InstallError,'does not activate'):
            mac.config(root)

    def test_unknown_launchd_failure_is_not_successful_absence(self):
        result=subprocess.CompletedProcess([],1,b'',b'Operation not permitted')
        with patch.object(mac,'run',return_value=result),self.assertRaisesRegex(mac.InstallError,'unavailable'):
            mac.launch_status(self.base,'runtime')
        result=subprocess.CompletedProcess([],113,b'',b'Could not find service example')
        with patch.object(mac,'run',return_value=result):self.assertFalse(mac.launch_status(self.base,'runtime'))

    def test_only_exact_argv_and_plist_source_establish_job_ownership(self):
        root=self.base/"Jess's Mac Ω";expected=mac.job(root,'runtime')['ProgramArguments']
        text='path = '+str(root/'jobs/runtime.plist')+'\narguments = {\n'+'\n'.join(expected)+'\n}\n'
        result=subprocess.CompletedProcess([],0,text.encode(),b'')
        with patch.object(mac,'run',return_value=result):self.assertTrue(mac.launch_status(root,'runtime'))
        result.stdout=text.replace('_run','_inventory').encode()
        with patch.object(mac,'run',return_value=result),self.assertRaisesRegex(mac.InstallError,'different job'):
            mac.launch_status(root,'runtime')

    def test_health_requires_owner_and_rejects_nonobject_response(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return self.raw
        r=Response()
        for raw,expected in [(b'[]',False),(b'null',False),(b'{"live":true,"component":"other"}',False),(b'{"live":true,"component":"process-runtime"}',True)]:
            r.raw=raw
            with patch.object(mac.urllib.request,'build_opener') as opener:
                opener.return_value.open.return_value=r
                self.assertEqual(mac.probe_body(8092,'live','runtime'),expected)

    def test_stop_waits_for_launchd_asynchronous_removal(self):
        with patch.object(mac,'launch_status',side_effect=[True,True,False]) as status,patch.object(mac.time,'sleep'):
            mac.wait_absent(self.base,'runtime')
        self.assertEqual(status.call_count,3)

    def test_restart_preflight_allows_time_wait_but_not_a_live_listener(self):
        server=socket.socket();server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        server.bind(('127.0.0.1',0));port=server.getsockname()[1];server.listen()
        client=socket.create_connection(('127.0.0.1',port));connection,_=server.accept()
        connection.close();client.recv(1);client.close();server.close()
        with socket.socket() as other:
            other.bind(('127.0.0.1',0));other_port=other.getsockname()[1]
        mac.check_ports({'runtime_port':port,'service_port':other_port})


if __name__=='__main__':unittest.main()
