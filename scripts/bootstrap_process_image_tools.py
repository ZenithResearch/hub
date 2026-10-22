"""Create and verify the minimal, offline host environment for image tooling.

This is a reviewed Darwin arm64 host profile, separate from the Linux image
interpreter. New host platforms require their own reviewed interpreter pin.
"""
from __future__ import annotations

import argparse
import base64
import csv
import email
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import sysconfig
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT/'config/process-image-host-tools.v1.json'
LOCK = ROOT/'config/process-image-host-tools.lock'
RECEIPT = 'image-host-tools-receipt.json'
NAMES = {'pip', 'packaging', 'pytest', 'iniconfig', 'pluggy', 'pygments'}
MODULES = NAMES | {'_pytest'}


class HostToolError(ValueError):
    pass


def require(value, message):
    if not value:
        raise HostToolError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity():
    return {'version': platform.python_version(), 'implementation': sys.implementation.name,
            'sys_platform': sys.platform, 'machine': platform.machine(),
            'executable_sha256': sha(Path(sys._base_executable).resolve())}


def admitted_profile():
    profile = json.loads(PROFILE.read_bytes())
    require(set(profile) == {'schema', 'python', 'packages'} and
            profile['schema'] == 'hub.component-image-host-tools.v1', 'Unsupported host profile')
    require(identity() == profile['python'], 'Host Python differs from reviewed interpreter')
    require(len(profile['packages']) == 6 and {p['name'] for p in profile['packages']} == NAMES,
            'Unexpected host tool closure')
    for p in profile['packages']:
        require(set(p) == {'name', 'version', 'filename', 'sha256'} and
                p['filename'] == p['name']+'-'+p['version']+'-py3-none-any.whl' and
                re.fullmatch('[a-f0-9]{64}', p['sha256']), 'Unsafe host wheel identity')
    expected_lock = ''.join(p['name']+'=='+p['version']+' --hash=sha256:'+p['sha256']+'\n'
                            for p in sorted(profile['packages'], key=lambda p: p['name']))
    require(LOCK.read_text() == expected_lock, 'Host lock/profile mismatch')
    return profile


def wheels(profile, wheelhouse):
    require(wheelhouse.is_dir() and not wheelhouse.is_symlink(), 'Missing host wheelhouse')
    require({p.name for p in wheelhouse.iterdir()} == {p['filename'] for p in profile['packages']},
            'Missing or extra host wheel')
    result = {}
    for package in profile['packages']:
        path = wheelhouse/package['filename']
        require(path.is_file() and not path.is_symlink() and path.stat().st_size < 16*1024**2 and
                sha(path) == package['sha256'], 'Host wheel digest mismatch')
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist(); names = [p.filename for p in entries]
            require(len(names) == len(set(names)) and len(names) < 10000 and
                    sum(p.file_size for p in entries) < 64*1024**2, 'Excessive/duplicate host wheel members')
            for p in entries:
                require(not p.filename.startswith('/') and '\\' not in p.filename and
                        '..' not in Path(p.filename).parts and not p.is_dir() and
                        (p.external_attr >> 16) & 0o170000 != 0o120000 and '.data' not in Path(p.filename).parts[0],
                        'Unsafe host wheel member')
            metadata = [n for n in names if n.endswith('.dist-info/METADATA')]
            records = [n for n in names if n.endswith('.dist-info/RECORD')]
            require(len(metadata) == len(records) == 1, 'Missing host wheel metadata')
            meta = email.message_from_bytes(archive.read(metadata[0]))
            require(meta['Name'].lower() == package['name'] and meta['Version'] == package['version'],
                    'Host wheel metadata mismatch')
            seen = set(); members = {}
            for name, digest, length in csv.reader(io.StringIO(archive.read(records[0]).decode())):
                require(name not in seen and name in names, 'Invalid host wheel RECORD'); seen.add(name)
                if name == records[0]:
                    require(digest == length == '', 'Invalid host RECORD self hash'); continue
                raw = archive.read(name)
                expected = 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
                require(digest == expected and int(length) == len(raw), 'Host wheel RECORD mismatch')
                members[name] = hashlib.sha256(raw).hexdigest()
            require(seen == set(names), 'Unrecorded host wheel file')
            result[package['name']] = {'record': records[0], 'members': members}
    return result


def environment_inventory(profile, admitted):
    prefix = Path(sys.prefix).resolve()
    require(sys.prefix != sys.base_prefix and not (prefix/'pyvenv.cfg').is_symlink(), 'Isolated host venv required')
    require('include-system-site-packages = false' in (prefix/'pyvenv.cfg').read_text(), 'Global site packages rejected')
    site = Path(sysconfig.get_path('purelib')).resolve()
    require(site.is_relative_to(prefix), 'Host site packages outside prefix')
    distributions = list(importlib.metadata.distributions())
    require(len(distributions) == 6 and {d.metadata['Name'].lower() for d in distributions} == NAMES,
            'Unexpected installed host closure')
    expected = {p['name']: p['version'] for p in profile['packages']}
    original_members = set()
    for dist in distributions:
        name = dist.metadata['Name'].lower()
        require(dist.version == expected[name] and Path(dist.locate_file('')).resolve() == site,
                'Host distribution version/origin mismatch')
        for name, digest in admitted[name]['members'].items():
            path = site/name
            require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(site) and sha(path) == digest,
                    'Installed host package differs from admitted wheel')
            original_members.add(name)
    # Pip writes these installation metadata files and console wrappers. They
    # are inventoried in the receipt; all package code must match upstream.
    generated = set()
    for record in admitted.values():
        info = record['record'].rsplit('/', 1)[0]
        generated.update(info+'/'+name for name in ('RECORD', 'INSTALLER', 'REQUESTED'))
    files = {}
    for path in site.rglob('*'):
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'Unsafe host installation file')
        if path.is_dir(): continue
        name = str(path.relative_to(site))
        # Bytecode is never used by isolated -B invocations; reject it anyway.
        require(name in original_members or name in generated, 'Unadmitted host installation file')
        files[name] = sha(path)
    for name in MODULES:
        spec = importlib.util.find_spec(name)
        require(spec is not None and spec.origin and Path(spec.origin).resolve().is_relative_to(site),
                'Host module origin outside isolated prefix')
        loaded = sys.modules.get(name)
        require(loaded is None or Path(loaded.__file__).resolve().is_relative_to(site),
                'Preloaded host module outside isolated prefix')
    return {'packages': expected, 'site_files': files}


def receipt_value(profile, wheelhouse):
    return {'schema': 'hub.component-image-host-receipt.v1', 'profile_sha256': sha(PROFILE),
            'lock_sha256': sha(LOCK), 'python': identity(), 'wheelhouse': str(wheelhouse.resolve()),
            'wheels': profile['packages'], 'installed': environment_inventory(profile, wheels(profile, wheelhouse))}


def verify_environment():
    profile = admitted_profile()
    path = Path(sys.prefix)/RECEIPT
    require(path.is_file() and not path.is_symlink() and path.stat().st_size < 2*1024**2, 'Missing host tools receipt')
    receipt = json.loads(path.read_bytes())
    require(receipt == receipt_value(profile, Path(receipt['wheelhouse'])), 'Host environment/receipt drift')
    return {'profile_sha256': sha(PROFILE), 'lock_sha256': sha(LOCK), 'python': profile['python'],
            'packages': {p['name']: p['version'] for p in profile['packages']}}


def create_environment(destination, wheelhouse):
    profile = admitted_profile(); wheels(profile, wheelhouse)
    require(not destination.exists(), 'Host environment already exists')
    venv.EnvBuilder(with_pip=False, symlinks=True).create(destination)
    interpreter = destination/'bin/python'
    home = destination/'installer-home'; home.mkdir(mode=0o700)
    environment = {'PATH': os.defpath, 'HOME': str(home.resolve()), 'PIP_CONFIG_FILE': os.devnull,
                   'PYTHONNOUSERSITE': '1'}
    pinned_pip = next(p for p in profile['packages'] if p['name'] == 'pip')
    # The only executable installer is the SHA-verified upstream pip wheel.
    code = 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("pip",run_name="__main__")'
    subprocess.run([str(interpreter), '-I', '-B', '-c', code, str((wheelhouse/pinned_pip['filename']).resolve()),
                    'install', '--isolated', '--no-index', '--find-links='+str(wheelhouse.resolve()),
                    '--require-hashes', '--only-binary=:all:', '--no-compile', '--no-cache-dir',
                    '--disable-pip-version-check', '-r', str(LOCK)], check=True, timeout=120, env=environment)
    subprocess.run([str(interpreter), '-I', '-B', str(Path(__file__).resolve()), 'receipt',
                    '--wheelhouse', str(wheelhouse.resolve())], check=True, timeout=30, env=environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'verify', 'receipt'))
    parser.add_argument('--environment', type=Path)
    parser.add_argument('--wheelhouse', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'create':
            require(args.environment and args.wheelhouse, 'Create-only prefix and wheelhouse required')
            create_environment(args.environment, args.wheelhouse)
            result = {'status': 'created', 'production_ready': False}
        elif args.action == 'receipt':
            require(args.wheelhouse and sys.prefix != sys.base_prefix, 'Isolated receipt inputs required')
            value = receipt_value(admitted_profile(), args.wheelhouse)
            path = Path(sys.prefix)/RECEIPT
            with path.open('xb') as output: output.write(canonical(value)+b'\n')
            result = {'status': 'recorded', 'receipt_sha256': sha(path)}
        else:
            result = {'status': 'verified', **verify_environment()}
        print(json.dumps(result, sort_keys=True)); return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
