"""Nonexecutable public fixture bytes; packaging tests do not activate authority."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/process_mac.py'
spec = importlib.util.spec_from_file_location('process_mac_native_bundle', SCRIPT)
mac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mac)


class NativeBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'repo'
        (self.repo / 'config').mkdir(parents=True)
        self.wheels = self.base / 'wheels'
        self.wheels.mkdir()
        self.native = self.base / 'native'
        self.native.mkdir()
        wheels = []
        for i in range(15):
            name = f'package_{i}-1-py3-none-any.whl'
            raw = f'public synthetic wheel {i}'.encode()
            (self.wheels / name).write_bytes(raw)
            wheels.append(dict(name=f'package-{i}', filename=name, version='1',
                               bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        roles = {'wallet-command': 'castalia-wallet-service',
                 'owner-setup': 'castalia-wallet-local-review-setup',
                 'result-attester': 'secs-frank-result',
                 'context-verifier': 'process-runtime-secs-check'}
        native = []
        for role, name in roles.items():
            raw = ('PUBLIC NONEXECUTABLE FIXTURE ' + role).encode()
            p = self.native / name
            p.write_bytes(raw)
            p.chmod(0o500)
            native.append(dict(role=role, filename=name, bytes=len(raw),
                               sha256=hashlib.sha256(raw).hexdigest(),
                               source_commit='a' * 40, mode=0o500))
        self.profile = dict(schema='hub.process-macos-release.v2', platform='darwin',
                            machine='arm64', python=[3, 12], components={}, wheels=wheels,
                            native_profile='local-review-v1', native=native)
        self.profile_path = self.repo / 'config/process-macos-pilot-release.v1.json'
        self.write_profile()
        p = patch.object(mac, 'ROOT', self.repo)
        p.start()
        self.addCleanup(p.stop)

    def write_profile(self):
        self.profile_path.write_text(json.dumps(self.profile))

    def bundle(self, name='bundle'):
        dest = self.base / name
        r = mac.bundle(self.wheels, dest, profile_name='local-review', nativehouse=self.native)
        return dest, r['bundle_sha256']

    def test_exact_native_closure_and_modes(self):
        dest, pin = self.bundle()
        self.assertEqual(mac.check_bundle(dest, pin), self.profile)
        self.assertEqual(set(p.name for p in (dest / 'native').iterdir()),
                         set(r['filename'] for r in self.profile['native']))
        self.assertTrue(all((dest / 'native' / r['filename']).stat().st_mode & 0o777 == 0o500
                            for r in self.profile['native']))

    def test_missing_native_denies_before_destination(self):
        (self.native / self.profile['native'][0]['filename']).unlink()
        with self.assertRaisesRegex(mac.InstallError, 'Native executable differs'):
            self.bundle()
        self.assertFalse((self.base / 'bundle').exists())

    def test_wrong_native_bytes_denies_before_destination(self):
        p = self.native / self.profile['native'][0]['filename']
        p.chmod(0o600)
        p.write_bytes(b'wrong')
        p.chmod(0o500)
        with self.assertRaisesRegex(mac.InstallError, 'Native executable differs'):
            self.bundle()
        self.assertFalse((self.base / 'bundle').exists())

    def test_duplicate_role_denies(self):
        self.profile['native'][1] = dict(self.profile['native'][0])
        self.write_profile()
        with self.assertRaisesRegex(mac.InstallError, 'Invalid native payload pin'):
            self.bundle()

    def test_unknown_role_denies(self):
        self.profile['native'][0]['role'] = 'generic-signer'
        self.write_profile()
        with self.assertRaisesRegex(mac.InstallError, 'Invalid native payload pin'):
            self.bundle()

    def test_unknown_native_field_denies(self):
        self.profile['native'][0]['qualified'] = True
        self.write_profile()
        with self.assertRaisesRegex(mac.InstallError, 'Invalid native payload record'):
            self.bundle()

    def test_null_placeholder_pin_denies(self):
        self.profile['native'][0]['sha256'] = None
        self.write_profile()
        with self.assertRaisesRegex(mac.InstallError, 'Invalid native payload pin'):
            self.bundle()

    def test_wrong_mode_and_symlink_deny(self):
        p = self.native / self.profile['native'][0]['filename']
        p.chmod(0o700)
        with self.assertRaisesRegex(mac.InstallError, 'Native executable differs'):
            self.bundle()
        p.chmod(0o500)
        target = self.base / 'public-original'
        p.rename(target)
        p.symlink_to(target)
        with self.assertRaisesRegex(mac.InstallError, 'Native executable differs'):
            self.bundle()

    def test_tampered_native_rejected_before_any_process(self):
        dest, pin = self.bundle()
        p = dest / 'native' / self.profile['native'][0]['filename']
        p.chmod(0o600)
        p.write_bytes(b'changed')
        p.chmod(0o500)
        with patch.object(mac, 'run') as runner, self.assertRaisesRegex(mac.InstallError, 'content differs'):
            mac.install(dest, self.base / 'install', pin)
        runner.assert_not_called()
        self.assertFalse((self.base / 'install').exists())

    def test_valid_install_copies_native_but_keeps_default_deny(self):
        dest, pin = self.bundle()
        target = self.base / 'Process-Pilot'
        class FakeVenv:
            def create(self, root):
                (root / 'bin').mkdir(parents=True)
                (root / 'bin/python').write_bytes(b'public fake interpreter never executed')
        def fake_run(argv, root, **kwargs):
            if '_seal' in argv:
                mac.save(root / 'installed-files.json', {})
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        with patch.object(mac, 'host'), patch.object(mac.venv, 'EnvBuilder', return_value=FakeVenv()), patch.object(mac, 'run', side_effect=fake_run):
            report = mac.install(dest, target, pin, 8192, 8191)
        self.assertEqual(report['authority'], 'pending')
        self.assertFalse((target / 'authority').exists())
        self.assertEqual((target / 'exports').stat().st_mode & 0o777, 0o700)
        self.assertEqual(mac.load(target / 'config/local.json')['mode'], 'local_review')
        self.assertEqual(mac.load(target / 'config/frank.json')['integrations']['actors'], [])
        self.assertEqual(mac.config(target)['runtime_port'], 8192)
        for row in self.profile['native']:
            p = target / 'native' / row['filename']
            self.assertEqual(p.read_bytes(), (dest / 'native' / row['filename']).read_bytes())
            self.assertEqual(p.stat().st_mode & 0o777, 0o500)

    def test_existing_install_is_not_overwritten(self):
        dest, pin = self.bundle()
        target = self.base / 'existing'
        target.mkdir(mode=0o700)
        (target / 'keep.db').write_bytes(b'previous')
        with patch.object(mac, 'host'), self.assertRaisesRegex(mac.InstallError, 'already exists'):
            mac.install(dest, target, pin)
        self.assertEqual((target / 'keep.db').read_bytes(), b'previous')

    def test_v1_cannot_smuggle_native_payloads(self):
        self.profile['schema'] = 'hub.process-macos-release.v1'
        with self.assertRaisesRegex(mac.InstallError, 'explicit local-review release'):
            mac.native_rows(self.profile)

    def test_default_v1_install_still_uses_installation_only_without_authority(self):
        profile = {k: v for k, v in self.profile.items() if k not in {'native', 'native_profile'}}
        profile['schema'] = 'hub.process-macos-release.v1'
        (self.repo/'config/process-macos-release.v1.json').write_text(json.dumps(profile))
        dest = self.base/'default-bundle'
        pin = mac.bundle(self.wheels, dest)['bundle_sha256']
        target = self.base/'ordinary-install'
        class FakeVenv:
            def create(self, root):
                (root/'bin').mkdir(parents=True)
                (root/'bin/python').write_bytes(b'public fake interpreter never executed')
        def fake_run(argv, root, **kwargs):
            if '_seal' in argv: mac.save(root/'installed-files.json', {})
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        with patch.object(mac, 'host'), patch.object(mac.venv, 'EnvBuilder', return_value=FakeVenv()), patch.object(mac, 'run', side_effect=fake_run):
            report = mac.install(dest, target, pin)
        self.assertEqual(report['authority'], 'pending')
        self.assertEqual(mac.config(target)['mode'], 'installation_only')
        self.assertEqual(mac.config(target)['runtime_port'], 8092)
        self.assertEqual(mac.load(target/'config/frank.json')['integrations']['actors'], [])
        self.assertFalse((target/'authority').exists())
        self.assertFalse((target/'exports').exists())


if __name__ == '__main__':
    unittest.main()
