#!/usr/bin/env python3
"""Offline per-user Mac installation and bounded owner-selected pilot lifecycle."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import tempfile
import urllib.error
import urllib.request
import venv
import zipfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Path.home()/'Library/Application Support/Zenith/Process'
COMPONENTS = {'runtime': 'process_runtime', 'service': 'service_management'}
# Packaging is not authority activation. These are fixed opt-in payload roles.
NATIVE_ROLES = {
    'wallet-command': 'castalia-wallet-service',
    'owner-setup': 'castalia-wallet-local-review-setup',
    'result-attester': 'secs-frank-result',
    'context-verifier': 'process-runtime-secs-check',
}
MAX_NATIVE_BYTES = 64 * 1024 * 1024


def native_rows(profile):
    if profile.get('schema') == 'hub.process-macos-release.v1':
        require('native' not in profile and 'native_profile' not in profile,
                'Native payloads require an explicit local-review release')
        return []
    require(set(profile) == {'schema', 'platform', 'machine', 'python', 'components',
                            'wheels', 'native_profile', 'native'}
            and profile['schema'] == 'hub.process-macos-release.v2'
            and profile['native_profile'] == 'local-review-v1',
            'Unsupported Mac release profile')
    rows = profile['native']
    require(type(rows) is list and len(rows) == len(NATIVE_ROLES),
            'Incomplete native payload closure')
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == {'role', 'filename', 'bytes', 'sha256',
                                                  'source_commit', 'mode'},
                'Invalid native payload record')
        role = row['role']
        require(type(role) is str and role in NATIVE_ROLES and role not in seen
                and row['filename'] == NATIVE_ROLES[role]
                and type(row['bytes']) is int and 0 < row['bytes'] <= MAX_NATIVE_BYTES
                and type(row['mode']) is int and row['mode'] == 0o500
                and type(row['sha256']) is str and re.fullmatch('[a-f0-9]{64}', row['sha256'])
                and type(row['source_commit']) is str and re.fullmatch('[a-f0-9]{40}', row['source_commit']),
                'Invalid native payload pin')
        seen.add(role)
    return rows


def check_native_payload(path, row):
    require(not path.is_symlink() and path.is_file()
            and path.stat().st_size == row['bytes']
            and stat.S_IMODE(path.stat().st_mode) == row['mode']
            and sha(path) == row['sha256'], 'Native executable differs from release profile')


class InstallError(ValueError):
    pass


def require(test, message):
    if not test:
        raise InstallError(message)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, sort_keys=True, indent=2)
        f.write('\n')


def load(path):
    return json.loads(Path(path).read_bytes())


def private(path, *, exists=True):
    require('..' not in Path(path).parts, 'Unsafe installation path')
    path = Path(os.path.abspath(path))
    require('..' not in Path(path).parts, 'Unsafe installation path')
    for p in (path, *path.parents):
        require(not p.is_symlink(), 'Installation paths cannot traverse symlinks')
        if p.exists():
            info = p.stat()
            require(info.st_uid in (0, os.getuid()), 'Installation ancestor has another owner')
            require(not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX,
                    'Installation ancestor is writable by other users')
    if exists:
        info = path.stat()
        require(info.st_uid == os.getuid() and not info.st_mode & 0o077,
                'Installation requires a private owner-controlled directory/file')
    return path


def host(profile):
    require(sys.platform == profile['platform'] and platform.machine() == profile['machine'],
            'This bundle requires an Apple silicon Mac')
    require(list(sys.version_info[:2]) == profile['python'], 'Install Python 3.12 before continuing')
    require(os.getuid() != 0, 'Run as your normal Mac account, without sudo')


def environment(root):
    return {'PATH': os.defpath, 'HOME': str(root/'home'), 'LANG': 'C.UTF-8',
            'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
            'PIP_CONFIG_FILE': os.devnull, 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}


def run(argv, root, timeout=30, *, check=True):
    # Spool bounded output; do not accumulate a runaway child in memory.
    with tempfile.TemporaryFile(dir=root/'logs') as out, tempfile.TemporaryFile(dir=root/'logs') as err:
        child = subprocess.Popen([str(x) for x in argv], cwd=root, env=environment(root),
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        deadline = time.monotonic()+timeout
        reason = None
        while child.poll() is None:
            if time.monotonic() >= deadline:
                reason = 'Local command timed out'
            if os.fstat(out.fileno()).st_size+os.fstat(err.fileno()).st_size > 2*1024*1024:
                reason = 'Local command output exceeded its bound'
            if reason:
                child.kill();child.wait(timeout=5);break
            time.sleep(.02)
        if os.fstat(out.fileno()).st_size+os.fstat(err.fileno()).st_size>2*1024*1024:reason='Local command output exceeded its bound'
        out.seek(0);err.seek(0)
        stdout,stderr=out.read(2*1024*1024),err.read(2*1024*1024)
        if reason or check and child.returncode:
            name='command-'+uuid.uuid4().hex
            (root/'logs'/(name+'.stdout.log')).write_bytes(stdout)
            (root/'logs'/(name+'.stderr.log')).write_bytes(stderr)
            raise InstallError((reason or 'Local command failed')+'; private command logs retained')
        return subprocess.CompletedProcess(argv,child.returncode,stdout,stderr)


def check_bundle(bundle, expected):
    bundle = Path(bundle).resolve()
    require(re.fullmatch('[a-f0-9]{64}', expected or '') and sha(bundle/'bundle.json') == expected,
            'Bundle manifest digest differs from the supplied release pin')
    manifest = load(bundle/'bundle.json')
    require(set(manifest) == {'schema', 'files'} and manifest['schema'] == 'hub.process-macos-bundle.v1',
            'Unsupported Mac bundle')
    for name, digest in manifest['files'].items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts and '\\' not in name,
                'Unsafe bundle member')
        p = bundle/name
        require(not any(q.is_symlink() for q in (p, *(bundle/q for q in p.relative_to(bundle).parents))
                        if q != Path('.')) and p.is_file() and sha(p) == digest,
                'Bundle content differs from release manifest')
    profile = load(bundle/'profile.json')
    native = native_rows(profile)
    require(len(profile['wheels']) == 15 and len({r['name'] for r in profile['wheels']}) == 15,
            'Incomplete Mac dependency closure')
    expected_files = {'profile.json', 'process_mac.py'} | {'wheels/'+r['filename'] for r in profile['wheels']}
    expected_files |= {'native/'+r['filename'] for r in native}
    require(set(manifest['files']) == expected_files, 'Bundle has missing or unexpected payloads')
    for row in profile['wheels']:
        p = bundle/'wheels'/row['filename']
        require(p.name == row['filename'] and p.suffix == '.whl' and sha(p) == row['sha256']
                and p.stat().st_size == row['bytes'], 'Wheel differs from release profile')
    for row in native:
        check_native_payload(bundle/'native'/row['filename'], row)
    return profile


def bundle(wheelhouse, destination, *, profile_name='installation', nativehouse=None):
    wheelhouse, destination = Path(wheelhouse).resolve(), Path(destination).absolute()
    require(profile_name in {'installation', 'local-review'}, 'Unsupported bundle profile')
    profile = load(ROOT/'config'/('process-macos-release.v1.json' if profile_name == 'installation'
                                 else 'process-macos-pilot-release.v1.json'))
    require((profile_name == 'installation') == (profile.get('schema') == 'hub.process-macos-release.v1'),
            'Bundle profile selection differs from release')
    native = native_rows(profile)
    require(not native or nativehouse is not None, 'Supply the pinned native payload directory')
    require(nativehouse is None or native, 'Native payloads are not part of the installation-only release')
    if native:
        nativehouse = Path(nativehouse)
        require(nativehouse.is_dir() and not nativehouse.is_symlink(), 'Unsafe native payload directory')
        for row in native:
            check_native_payload(nativehouse/row['filename'], row)
    require(not destination.exists(), 'Bundle destination already exists')
    for row in profile['wheels']:
        p = wheelhouse/row['filename']
        require(p.is_file() and not p.is_symlink() and sha(p) == row['sha256'], 'Mac wheel pin mismatch')
    destination.mkdir(mode=0o700, parents=True)
    (destination/'wheels').mkdir(mode=0o700)
    for row in profile['wheels']:
        shutil.copyfile(wheelhouse/row['filename'], destination/'wheels'/row['filename'])
    if native:
        (destination/'native').mkdir(mode=0o700)
        for row in native:
            p = destination/'native'/row['filename']
            shutil.copyfile(nativehouse/row['filename'], p)
            p.chmod(row['mode'])
    shutil.copyfile(__file__, destination/'process_mac.py')
    save(destination/'profile.json', profile)
    files = {str(p.relative_to(destination)): sha(p) for p in destination.rglob('*') if p.is_file()}
    save(destination/'bundle.json', {'schema': 'hub.process-macos-bundle.v1', 'files': files})
    digest = sha(destination/'bundle.json')
    launcher = '''#!/bin/bash
set -euo pipefail
bundle_dir="$(cd -- "$(dirname -- "$0")" && pwd -P)"
for python in /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
  if [[ -x "$python" ]]; then
    exec "$python" -I -S -B "$bundle_dir/process_mac.py" install --bundle "$bundle_dir" --bundle-sha256 REPLACE_DIGEST "$@"
  fi
done
echo 'Python 3.12 is required. Install it, then open Install.command again.' >&2
exit 2
'''.replace('REPLACE_DIGEST', digest)
    if native:
        launcher = launcher.replace('install --bundle', 'install --root "$HOME/Library/Application Support/Zenith/Process-Pilot" --runtime-port 8192 --service-port 8191 --bundle')
    (destination/'Install.command').write_text(launcher)
    (destination/'Install.command').chmod(0o700)
    result = {'bundle': str(destination), 'bundle_sha256': digest, 'wheel_count': 15}
    if native:
        result['native_count'] = len(native)
    return result


def inventory(root, *, seal=False):
    require(Path(sys.executable).absolute() == root/'venv/bin/python', 'Inventory requires the installed interpreter')
    site=root/'venv/lib/python3.12/site-packages'
    distributions={d.metadata['Name'].lower().replace('_','-'):d for d in metadata.distributions(path=[str(site)])}
    profile = load(root/'profile.json')
    result = {}
    for row in profile['wheels']:
        dist = distributions[row['name'].replace('_','-')]
        require(dist.version == row['version'], 'Installed package version differs from release')
        with zipfile.ZipFile(root/'wheels'/row['filename']) as archive:
            for member in archive.namelist():
                if member.endswith(('/', '/RECORD')):
                    continue
                require(not Path(member).is_absolute() and '..' not in Path(member).parts,
                        'Unsafe wheel payload')
                if '.data/data/' in member:
                    target = root/'venv'/member.split('.data/data/', 1)[1]
                else:
                    require('.data/' not in member, 'Unsupported wheel layout')
                    target = Path(dist.locate_file(member))
                require(not target.is_symlink() and target.resolve().is_relative_to(root/'venv') and not any(q.is_symlink() for q in target.parents if q.is_relative_to(root/'venv'))
                        and target.read_bytes() == archive.read(member), 'Installed wheel payload changed')
        result[row['name']] = dist.version
    require(not any(site.glob('*.pth')) and not (site/'sitecustomize.py').exists()
            and not (site/'usercustomize.py').exists(), 'Unexpected executable site configuration')
    files={}
    for path in site.rglob('*'):
        require(not path.is_symlink(), 'Symlink in installed packages')
        if path.is_file():files[str(path.relative_to(site))]=sha(path)
    record=root/'installed-files.json'
    if seal:save(record,files)
    else:require(files == load(record), 'Installed package inventory changed')
    return result


def installed(root):
    root = private(root)
    receipt = load(root/'installed.json')
    host(load(root/'profile.json'))
    require(receipt['schema'] == 'hub.process-macos-installed.v1' and receipt['root'] == str(root),
            'Installation was relocated; reinstall at its final path')
    require(sha(Path(sys._base_executable).resolve()) == receipt['python_sha256'],
            'Python changed; reinstall with the current interpreter')
    check_bundle(root, receipt['bundle_sha256'])
    require(sha(root/'control.py') == receipt['control_sha256'], 'Installed controller changed')
    require(sha(root/'installed-files.json') == receipt['installed_files_sha256'], 'Installed inventory changed')
    run([root/'venv/bin/python', '-I', '-S', '-B', root/'control.py', '_inventory', '--root', root], root)
    return receipt


def install(source, root, expected, runtime_port=8092, service_port=8091):
    profile = check_bundle(source, expected)
    host(profile)
    require(sha(__file__) == load(Path(source)/'bundle.json')['files']['process_mac.py'],
            'Use the controller delivered in this exact bundle')
    root = private(root, exists=False)
    require(not root.exists(), 'Installation already exists; data is never overwritten')
    require(type(runtime_port) is int and type(service_port) is int and runtime_port != service_port
            and all(1024 <= p <= 65535 for p in (runtime_port, service_port)), 'Invalid or duplicate ports')
    mode = 'local_review' if profile.get('native_profile') == 'local-review-v1' else 'installation_only'
    if mode == 'local_review':
        require(root.name == 'Process-Pilot' and runtime_port == 8192 and service_port == 8191,
                'Local review requires a separate Process-Pilot installation on ports 8192/8191')
    require(shutil.disk_usage(root.parent if root.parent.exists() else Path.home()).free >= 128*1024**2,
            'At least 128 MiB free disk space is required')
    root.mkdir(mode=0o700, parents=True)
    os.chmod(root, 0o700)
    for name in ('home', 'config', 'data', 'logs', 'jobs', *(['exports'] if mode == 'local_review' else [])):
        (root/name).mkdir(mode=0o700)
    shutil.copyfile(Path(source)/'bundle.json', root/'bundle.json')
    for name in load(Path(source)/'bundle.json')['files']:
        target = root/name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(Path(source)/name, target)
        if name in {'native/'+row['filename'] for row in native_rows(profile)}:
            target.chmod(0o500)
    shutil.copyfile(__file__, root/'control.py')
    venv.EnvBuilder(with_pip=True, symlinks=True).create(root/'venv')
    python = root/'venv/bin/python'
    wheels = [root/'wheels'/row['filename'] for row in profile['wheels']]
    command = [python, '-I', '-B', '-m', 'pip', 'install', '--isolated', '--no-index', '--no-deps',
               '--no-compile', '--no-cache-dir', '--disable-pip-version-check', *wheels]
    result = run(command, root, timeout=180, check=False)
    (root/'logs/install.log').write_bytes(result.stdout+result.stderr)
    require(result.returncode == 0, 'Offline package install failed; retain root and inspect logs/install.log')
    run([python, '-I', '-B', '-m', 'pip', 'check'], root)
    config = {'schema': 'hub.process-macos-config.v1', 'mode': mode,
              'runtime_port': runtime_port, 'service_port': service_port}
    save(root/'config/local.json', config)
    save(root/'config/frank.json', {'schema': 'frank.config.v1', 'environment': 'production',
         'runtime_endpoint': 'http://127.0.0.1:'+str(runtime_port), 'journal': str(root/'data/frank.db'),
         'allow_local_dev_untrusted': False,
         'integrations': {'schema': 'frank.integrations.v1', 'integrations': [], 'actors': []}})
    run([python, '-I', '-S', '-B', root/'control.py', '_seal', '--root', root], root)
    save(root/'installed.json', {'schema': 'hub.process-macos-installed.v1', 'root': str(root),
         'bundle_sha256': expected, 'control_sha256': sha(root/'control.py'),
         'python_sha256': sha(Path(sys._base_executable).resolve()),
         'installed_files_sha256':sha(root/'installed-files.json'), 'authority_qualified': False})
    (root/'Process.command').write_text('''#!/bin/bash
set -euo pipefail
install_dir="$(cd -- "$(dirname -- "$0")" && pwd -P)"
action="${1:-doctor}"
if (($#)); then shift; fi
exec "$install_dir/venv/bin/python" -I -S -B "$install_dir/control.py" "$action" --root "$install_dir" "$@"
''')
    (root/'Process.command').chmod(0o700)
    return {'installed': True, 'root': str(root), 'authority': 'pending', 'external_effects': False}


def config(root):
    value = load(private(root/'config/local.json'))
    require(set(value) == {'schema', 'mode', 'runtime_port', 'service_port'}
            and value['schema'] == 'hub.process-macos-config.v1' and value['mode'] in {'installation_only', 'local_review'}
            and all(type(value[k]) is int and 1024 <= value[k] <= 65535 for k in ('runtime_port', 'service_port'))
            and value['runtime_port'] != value['service_port'], 'Invalid local installation configuration')
    if value['mode'] == 'local_review':
        require(load(root/'profile.json').get('native_profile') == 'local-review-v1'
                and root.name == 'Process-Pilot'
                and value['runtime_port'] == 8192 and value['service_port'] == 8191,
                'Local-review mode requires its isolated pinned release and ports')
    frank = load(private(root/'config/frank.json'))
    require(frank['environment'] == 'production' and frank['allow_local_dev_untrusted'] is False
            and frank['integrations'] == {'schema': 'frank.integrations.v1', 'integrations': [], 'actors': []},
            'Installation doctor does not activate providers or development authority')
    return value


# The release opts into this bounded physical-Mac pilot. No default install is
# upgraded in place, and the browser never supplies filenames or argv.
PILOT_ACTIONS = {'pilot-setup', 'pilot-initialize', 'pilot-next', 'pilot-inspect',
                 'pilot-resume', 'pilot-renew', 'pilot-export'}
PILOT_RUN = 'local-review-pilot-20261017-001'
PILOT_SELECTION = 'config/pilot-controller.json'
PILOT_OPERATION = 'data/pilot-operation.json'


def bounded_private(path, maximum=65536):
    path = private(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and 0 < before.st_size <= maximum, 'Invalid private pilot input')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(fd)
        require(len(raw) == before.st_size and
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                'Pilot input changed while being read')
        return raw
    finally:
        os.close(fd)


def closed_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'Duplicate pilot metadata key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(InstallError('Invalid pilot number')))
    except (ValueError, RecursionError):
        raise InstallError('Invalid pilot metadata') from None


def pilot_profile(root):
    value = config(root)
    require(value['mode'] == 'local_review' and root.name == 'Process-Pilot'
            and value['runtime_port'] == 8192 and value['service_port'] == 8191,
            'This action requires the isolated local-review installation')
    profile = load(root/'profile.json')
    require(profile.get('native_profile') == 'local-review-v1' and len(native_rows(profile)) == 4,
            'Local review requires the pinned native release')
    return value


def pilot_inputs(root, expected=None, *, workflow=False):
    pilot_profile(root)
    raw = bounded_private(root/PILOT_SELECTION, 16384)
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None:
        require(type(expected) is str and re.fullmatch('[a-f0-9]{64}', expected)
                and digest == expected, 'Pilot selection changed after review')
    selection = closed_json(raw)
    require(type(selection) is dict and set(selection) == {'schema', 'setup_plan_sha256', 'workflow_sha256'}
            and selection['schema'] == 'hub.process-macos-pilot.v1'
            and type(selection['setup_plan_sha256']) is str
            and re.fullmatch('[a-f0-9]{64}', selection['setup_plan_sha256'])
            and (selection['workflow_sha256'] is None or type(selection['workflow_sha256']) is str
                 and re.fullmatch('[a-f0-9]{64}', selection['workflow_sha256'])),
            'Invalid owner-selected pilot configuration')
    require(not workflow or selection['workflow_sha256'] is not None,
            'Select the exact workflow after native setup before execution')
    for name, key in [('config/pilot-setup-plan.json', 'setup_plan_sha256'),
                      ('config/pilot-workflow.json', 'workflow_sha256')]:
        if selection[key] is None:
            continue
        require(hashlib.sha256(bounded_private(root/name)).hexdigest() == selection[key],
                'Pilot input differs from owner selection')
    plan = closed_json(bounded_private(root/'config/pilot-setup-plan.json', 16384))
    require(type(plan) is dict and plan.get('schema') == 'castalia.wallet-local-review-setup.v1'
            and plan.get('pilot_root') == str(root), 'Owner plan selects another installation')
    return selection, digest


def pilot_complete(root, selection):
    raw = bounded_private(root/'authority/COMPLETE.json', 65536)
    receipt = closed_json(raw)
    require(type(receipt) is dict and receipt.get('schema') == 'castalia.wallet-local-review-setup-complete.v1'
            and receipt.get('plan_sha256') == selection['setup_plan_sha256']
            and receipt.get('root') == str(root) and receipt.get('run_id') == PILOT_RUN
            and receipt.get('activation') is False and type(receipt.get('configuration_hashes')) is dict,
            'Pilot setup is incomplete or selects another owner plan')
    hashes = receipt['configuration_hashes']
    require(set(hashes) == {'command-authority.json', 'wallet-command.json', 'runtime-policy.json',
                            'runtime-receiver.json', 'result-authority.json', 'result-policy.json',
                            'frank-local-review.json'}, 'Incomplete pilot configuration closure')
    for name in hashes:
        digest = hashes.get(name)
        require(type(digest) is str and re.fullmatch('[a-f0-9]{64}', digest)
                and hashlib.sha256(bounded_private(root/'authority/config'/name)).hexdigest() == digest,
                'Pilot authority configuration changed')
    return receipt


def pilot_cli_cleanup(root):
    path = root/'data/frank-local-review-cleanup.json'
    # Presence, including malformed or substituted state, blocks new native
    # launches. Saved identifiers never authorize a signal or deletion.
    return path.exists() or path.is_symlink()


def pilot_operation(root):
    path = root/PILOT_OPERATION
    if not path.exists() and not path.is_symlink():
        return None
    value = closed_json(bounded_private(path, 4096))
    require(type(value) is dict and set(value) == {'schema', 'operation_id', 'action', 'state'}
            and value['schema'] == 'hub.local-review.operation.v1'
            and type(value['operation_id']) is str and re.fullmatch('[a-f0-9]{32}', value['operation_id'])
            and value['action'] in PILOT_ACTIONS
            and value['state'] in {'running', 'cleanup_unresolved'}, 'Invalid pilot operation state')
    return value


def finish_pilot_operation(root, operation, *, unresolved=False):
    # A retained operation is a reason to stop. It is never authority to signal a
    # PID saved by an earlier process, or to erase another operation's state.
    require(pilot_operation(root) == operation, 'Pilot operation state changed')
    path = root/PILOT_OPERATION
    if unresolved:
        replacement = {**operation, 'state': 'cleanup_unresolved'}
        temporary = root/'data'/('pilot-operation-'+operation['operation_id']+'.next')
        save(temporary, replacement)
        os.replace(temporary, path)
    else:
        path.unlink()
    directory = os.open(root/'data', os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def run_pilot(argv, root, action, *, timeout=90):
    require(pilot_operation(root) is None and not pilot_cli_cleanup(root),
            'Pilot process cleanup is unresolved; inspect retained operation state')
    operation = {'schema': 'hub.local-review.operation.v1', 'operation_id': uuid.uuid4().hex,
                 'action': action, 'state': 'running'}
    save(root/PILOT_OPERATION, operation)
    with (root/PILOT_OPERATION).open('rb') as state:
        os.fsync(state.fileno())
    directory = os.open(root/'data', os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    # This one-shot CLI owns its detached native helpers. SIGINT gives its async
    # finally blocks time to drain them before the outer Node deadline.
    with tempfile.TemporaryFile(dir=root/'logs') as out, tempfile.TemporaryFile(dir=root/'logs') as err:
        child = None
        reason = None
        forced = False
        try:
            child = subprocess.Popen([str(x) for x in argv], cwd=root, env=environment(root),
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            deadline = time.monotonic() + timeout
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    reason = 'Pilot command timed out'
                if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > 2*1024*1024:
                    reason = 'Pilot command output exceeded its bound'
                if reason:
                    child.send_signal(signal.SIGINT)
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        forced = True
                        # Persist the uncertainty before forced termination.
                        finish_pilot_operation(root, operation, unresolved=True)
                        child.kill()
                        child.wait(timeout=5)
                    break
                time.sleep(.02)
            if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > 2*1024*1024:
                reason = 'Pilot command output exceeded its bound'
            out.seek(0); err.seek(0)
            stdout, stderr = out.read(2*1024*1024), err.read(2*1024*1024)
            if child.returncode not in (0, 2, 3) or pilot_cli_cleanup(root):
                if not forced:
                    finish_pilot_operation(root, operation, unresolved=True)
                forced = True
                reason = reason or 'Pilot cleanup ownership is unresolved'
            if reason or child.returncode:
                name = 'pilot-'+operation['operation_id']
                for suffix, data in [('stdout', stdout), ('stderr', stderr)]:
                    with (root/'logs'/(name+'.'+suffix+'.log')).open('xb') as stream:
                        os.fchmod(stream.fileno(), 0o600); stream.write(data)
                raise InstallError((reason or 'Pilot command denied or outcome unknown')+'; private logs retained')
            return closed_json(stdout)
        finally:
            # A failed launch has no child helpers. An exceptional wait or forced
            # termination retains the marker rather than reopening the pilot.
            if not forced and (child is None or child.returncode in (0, 2, 3)):
                primary = sys.exc_info()[0] is not None
                try:
                    finish_pilot_operation(root, operation)
                except (InstallError, OSError):
                    # The retained/replaced marker continues to deny new work.
                    if not primary:
                        raise


def pilot_report(root):
    result = {'mode': 'local_review', 'run_id': PILOT_RUN, 'setup': 'not_selected',
              'cleanup_unresolved': pilot_operation(root) is not None or pilot_cli_cleanup(root),
              'workflow': None, 'external_effects': False}
    selected = root/PILOT_SELECTION
    if not selected.exists() and not selected.is_symlink():
        return result
    selection, _ = pilot_inputs(root)
    result['workflow_selected'] = selection['workflow_sha256'] is not None
    authority = root/'authority'
    if not authority.exists() and not authority.is_symlink():
        result['setup'] = 'absent'
        return result
    if not (authority/'COMPLETE.json').exists():
        result['setup'] = 'partial'
        return result
    pilot_complete(root, selection)
    result['setup'] = 'configured'
    # This is only local immutable configuration validation, not a network
    # request or a readiness/authorization assertion.
    result['authorization'] = 'checked_per_request'
    observation = root/'data/pilot-observation.json'
    if observation.exists() or observation.is_symlink():
        result['workflow'] = closed_json(bounded_private(observation, 65536))
    return result


def pilot_admission(root, selection):
    # This existing receiver operator changes only the isolated new-run gate.
    # It supplies no service permission, key or provider authority.
    plan_raw = bounded_private(root/'config/pilot-setup-plan.json', 16384)
    require(hashlib.sha256(plan_raw).hexdigest() == selection['setup_plan_sha256'],
            'Owner setup plan changed')
    plan = closed_json(plan_raw)
    policy = {'schema': 'runtime.new-run-admission.v1', 'enabled': True,
              'composition_digest': plan['composition_sha256']}
    path = root/'config/runtime-admission.json'
    if path.exists() or path.is_symlink():
        require(closed_json(bounded_private(path, 4096)) == policy,
                'Runtime admission differs from the selected composition')
        return
    fd, temporary = tempfile.mkstemp(prefix='.pilot-admission-', dir=root/'config')
    request = Path(temporary)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({'schema': 'runtime.new-run-admission-apply.v1', 'policy': policy,
                       'expected_digest': None}, stream, sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        run([root/'venv/bin/python', '-I', '-B', '-m', 'process_runtime.admission',
             'apply', '--policy', path, '--request-file', request], root)
        require(closed_json(bounded_private(path, 4096)) == policy,
                'Runtime admission publication differs from its selected policy')
    finally:
        request.unlink(missing_ok=True)


def pilot_cli(root, selection, receipt, action):
    require(action in {'initialize', 'next', 'inspect', 'resume', 'renew', 'export'},
            'Unsupported fixed local-review action')
    master = root/'authority/config/frank-local-review.json'
    return [root/'venv/bin/python', '-I', '-B', '-m', 'frank_executor.local_review_cli', action,
            '--config', master, '--config-sha256', receipt['configuration_hashes']['frank-local-review.json'],
            '--workflow', root/'config/pilot-workflow.json', '--workflow-sha256', selection['workflow_sha256']]


def retain_pilot_inspection(root, value):
    require(type(value) is dict and set(value) == {'schema', 'run_id', 'run_state', 'recoverable',
            'export_available', 'steps', 'pending_commands', 'unresolved_effects'}
            and value['schema'] == 'frank.local-review.inspection.v1' and value['run_id'] == PILOT_RUN
            and type(value['run_state']) is str and 0 < len(value['run_state']) <= 64
            and type(value['recoverable']) is bool and type(value['export_available']) is bool
            and all(type(value[key]) is list for key in ('steps', 'pending_commands', 'unresolved_effects')),
            'Local-review inspection returned an invalid summary')
    observation = {**value, 'schema': 'hub.local-review.observation.v1',
                   'observed_at': int(time.time()*1000)}
    path = root/'data/pilot-observation.json'
    temporary = root/'data'/('pilot-observation-'+uuid.uuid4().hex+'.next')
    save(temporary, observation)
    with temporary.open('rb') as source:
        os.fsync(source.fileno())
    require(temporary.stat().st_size <= 65536, 'Pilot inspection exceeds its retained bound')
    os.replace(temporary, path)
    directory = os.open(root/'data', os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return observation


def pilot_action(root, action, expected):
    installed(root)
    require(expected is not None, 'Supply the reviewed pilot selection pin')
    require(action in PILOT_ACTIONS, 'Unsupported pilot action')
    selection, digest = pilot_inputs(root, expected, workflow=action != 'pilot-setup')
    require(pilot_operation(root) is None and not pilot_cli_cleanup(root), 'Pilot cleanup is unresolved')
    if action == 'pilot-setup':
        require(not (root/'authority').exists() and not (root/'authority').is_symlink(),
                'Authority root already exists; partial state is retained, never reset')
        return run_pilot([root/'native/castalia-wallet-local-review-setup', 'create', '--plan',
                          root/'config/pilot-setup-plan.json', '--plan-sha256', selection['setup_plan_sha256']],
                         root, action, timeout=90)
    receipt = pilot_complete(root, selection)
    if action == 'pilot-initialize':
        pilot_admission(root, selection)
    command = action.removeprefix('pilot-')
    result = run_pilot(pilot_cli(root, selection, receipt, command), root, action, timeout=90)
    if command == 'inspect':
        return retain_pilot_inspection(root, result)
    # Preserve a successful mutation if the separate observation is unavailable.
    # A retained cleanup marker still blocks every subsequent native launch.
    try:
        # Inspection authenticates the run plus every committed output/receipt.
        # Reuse the manual inspection budget as the observed run grows.
        observed = run_pilot(pilot_cli(root, selection, receipt, 'inspect'),
                             root, 'pilot-inspect', timeout=90)
        retain_pilot_inspection(root, observed)
        inspection = 'retained'
    except (InstallError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        inspection = 'unavailable'
    return {'action': action, 'action_completed': True, 'inspection': inspection}


def label(root, component):
    require(component in COMPONENTS, 'Unknown component')
    identity = hashlib.sha256((str(os.getuid())+':'+str(root)).encode()).hexdigest()[:20]
    return 'ca.zenith.process.'+identity+'.'+component


def job(root, component):
    return {'Label': label(root, component),
        'ProgramArguments': [str(root/'venv/bin/python'), '-I', '-S', '-B', str(root/'control.py'),
                             '_run', '--root', str(root), '--component', component],
        'RunAtLoad': True, 'KeepAlive': False, 'ExitTimeOut': 30,
        'StandardOutPath': str(root/'logs'/f'{component}.log'),
        'StandardErrorPath': str(root/'logs'/f'{component}.log')}


def launch_status(root, component):
    service = f'gui/{os.getuid()}/{label(root, component)}'
    result = run(['/bin/launchctl', 'print', service], root, check=False)
    if result.returncode:
        require(result.returncode == 113 and b'Could not find service' in result.stdout+result.stderr,
                'Launchd job status is unavailable; absence cannot be established')
        return False
    text = result.stdout.decode('utf-8')
    arguments = re.search(r'^\s*arguments = \{\s*\n(.*?)^\s*\}', text, re.M|re.S)
    actual = [line.strip() for line in arguments[1].splitlines() if line.strip()] if arguments else []
    source = re.search(r'^\s*path = (.+)$', text, re.M)
    require(actual == job(root, component)['ProgramArguments'] and source is not None
            and source[1].strip() == str(root/'jobs'/f'{component}.plist'),
            'A different job is using this install label; refusing to adopt or stop it')
    return True


def wait_absent(root,component):
    deadline=time.monotonic()+10
    while launch_status(root,component):
        require(time.monotonic()<deadline,'Managed job remains registered')
        time.sleep(.05)


def probe_body(port, route, component):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        try:
            response = opener.open(f'http://127.0.0.1:{port}/{route}', timeout=1)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            raw=response.read(16385)
            if len(raw)>16384:return False
            value = json.loads(raw)
            return (type(value) is dict and response.status == 200 and value.get(route) is True
                    and value.get('component',value.get('service')) == {'runtime':'process-runtime','service':'service-management'}[component])
    except (OSError, ValueError):
        return False


def probe(root, component, route, timeout=2):
    # A child deadline also bounds slow headers/trickled bodies, not just sockets.
    port=config(root)[component+'_port']
    try:
        result=run([root/'venv/bin/python','-I','-S','-B',root/'control.py','_probe',
                    '--root',root,'--component',component,'--runtime-port',str(port),'--route',route],
                   root,timeout=timeout)
        return json.loads(result.stdout) is True
    except (InstallError, OSError, ValueError):
        return False


def owns_listener(root, component):
    marker=root/'logs'/f'{component}-process.json'
    if not marker.exists():return False
    value=load(marker)
    status=run(['/bin/launchctl','print',f'gui/{os.getuid()}/{label(root,component)}'],root,check=False)
    pid=re.search(rb'^\s*pid = (\d+)\s*$',status.stdout,re.M)
    if not pid or int(pid[1])!=value['wrapper_pid']:return False
    port=config(root)[component+'_port']
    listener=run(['/usr/sbin/lsof','-nP','-a','-p',str(value['child_pid']),
                  '-iTCP@127.0.0.1:'+str(port),'-sTCP:LISTEN','-t'],root,timeout=3,check=False)
    return listener.returncode==0 and listener.stdout.strip()==str(value['child_pid']).encode()


def doctor(root):
    installed(root)
    value = config(root)
    packages = json.loads(run([root/'venv/bin/python', '-I', '-S', '-B', root/'control.py',
                              '_inventory', '--root', root], root).stdout)
    frank = json.loads(run([root/'venv/bin/python', '-I', '-B', '-m', 'frank_executor',
                          'check-config', '--config', root/'config/frank.json'], root).stdout)
    states={}
    for name in COMPONENTS:
        managed=launch_status(root,name) and owns_listener(root,name)
        states[name]={'managed':managed,'live':managed and probe(root,name,'live'),
                      'ready':managed and probe(root,name,'ready')}
    result = {'installed': True, 'packages': packages, 'frank': frank,'components':states,
              'authority':'pending','pilot_ready':False,'external_effects':False}
    if value['mode'] == 'local_review':
        try:
            result['pilot'] = pilot_report(root)
        except (InstallError, OSError, ValueError, TypeError, KeyError):
            result['pilot'] = {'mode': 'local_review', 'setup': 'invalid',
                               'cleanup_unresolved': True, 'workflow': None, 'external_effects': False}
    return result


def check_ports(value):
    for name in COMPONENTS:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:
                s.bind(('127.0.0.1', value[name+'_port']))
            except OSError as e:
                raise InstallError('Component port is occupied; no service was stopped') from e


def start(root):
    installed(root)
    value = config(root)
    require(not any(launch_status(root, name) for name in COMPONENTS), 'Already registered; use status or stop first')
    if value.get('mode', 'installation_only') == 'local_review':
        selection, _ = pilot_inputs(root)
        require(pilot_operation(root) is None and not pilot_cli_cleanup(root), 'Pilot cleanup is unresolved')
        pilot_complete(root, selection)
    check_ports(value)
    started = []
    try:
        for name in COMPONENTS:
            path = root/'jobs'/f'{name}.plist'
            expected = plistlib.dumps(job(root, name), sort_keys=True)
            if path.exists():
                require(private(path).read_bytes() == expected, 'Managed plist changed')
            else:
                with path.open('xb') as f:
                    os.fchmod(f.fileno(), 0o600); f.write(expected)
            log = root/'logs'/f'{name}.log'
            if not log.exists():
                log.touch(mode=0o600)
            private(log)
            marker_path=root/'logs'/f'{name}-process.json'
            previous_generation=load(marker_path).get('generation') if marker_path.exists() else None
            started.append(name)
            run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', path], root)
            deadline = time.monotonic()+15
            while True:
                require(time.monotonic() < deadline, 'Component did not start; inspect private component log')
                if marker_path.exists():
                    marker = load(marker_path)
                    if marker['generation'] != previous_generation:
                        terminal = root/'logs'/f'{name}-shutdown.json'
                        require(not terminal.exists() or load(terminal).get('generation') != marker['generation'],
                                'Component exited during startup; no foreign listener was adopted')
                        if owns_listener(root,name) and probe(root,name,'live',timeout=max(.1,min(2,deadline-time.monotonic()))):
                            break
                time.sleep(.1)
        report=doctor(root)
        require(all(state['managed'] and state['live'] and not state['ready'] for state in report['components'].values()),
                'Final component liveness or default-deny readiness differs; stopping this installation')
        return report
    except BaseException:
        unresolved = []
        for name in reversed(started):
            try:
                if launch_status(root, name):
                    run(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{label(root, name)}'], root,timeout=40)
                wait_absent(root,name)
            except (InstallError, OSError, subprocess.SubprocessError):
                unresolved.append(name)
        if unresolved:
            raise InstallError('Startup failed and owned job cleanup is unresolved; run stop before retrying')
        raise


def stop(root):
    # Stop remains available even if package or interpreter pins drift.
    root = private(root)
    errors=[]
    for name in reversed(COMPONENTS):
        try:
            if launch_status(root, name):
                run(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{label(root, name)}'], root,timeout=40)
        except (InstallError,OSError,subprocess.SubprocessError):errors.append(name)
    require(not errors,'Some owned jobs could not be stopped or inspected; retry stop')
    for name in COMPONENTS:wait_absent(root,name)
    for name in COMPONENTS:
        marker = root/'logs'/f'{name}-process.json'
        if marker.exists():
            child_pid = load(marker)['child_pid']
            deadline = time.monotonic()+5
            while True:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                require(time.monotonic() < deadline, 'Child process absence is unresolved after stop')
                time.sleep(.1)
    results = {name:load(root/'logs'/f'{name}-shutdown.json') for name in COMPONENTS
               if (root/'logs'/f'{name}-shutdown.json').exists()}
    return {'stopped': True, 'data_preserved': True, 'shutdown': results}


def supervise(root, component):
    installed(root)
    value = config(root)
    argv = [root/'venv/bin/python', '-I', '-B', '-m', COMPONENTS[component],
            '--database', root/'data'/f'{component}.db', '--port', str(value[component+'_port']),
            '--environment', 'production']
    if value['mode'] == 'local_review' and component == 'runtime':
        selection, _ = pilot_inputs(root)
        pilot_complete(root, selection)
        argv += ['--receiver-config', root/'authority/config/runtime-receiver.json',
                 '--admission-policy', root/'config/runtime-admission.json']
    child = None
    stopping = False
    def stopped(signum, frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGINT if component == 'runtime' else signal.SIGTERM)
    signal.signal(signal.SIGTERM, stopped)
    signal.signal(signal.SIGINT, stopped)
    child = subprocess.Popen([str(x) for x in argv], cwd=root, env=environment(root), start_new_session=False)
    if stopping:stopped(signal.SIGTERM,None)
    generation = uuid.uuid4().hex
    forced = False
    try:
        marker = root/'logs'/f'{component}-process.json'
        temporary = marker.with_suffix('.next')
        with temporary.open('w') as f:
            os.fchmod(f.fileno(), 0o600);json.dump({'generation':generation,'wrapper_pid':os.getpid(),'child_pid':child.pid},f)
        os.replace(temporary,marker)
        while child.poll() is None:
            if stopping:
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    forced = True
                    child.kill()
                    child.wait(timeout=5)
                break
            time.sleep(.1)
    finally:
        if child.poll() is None:
            stopped(signal.SIGTERM,None)
            try:child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                forced=True;child.kill();child.wait(timeout=5)
    result = {'component': component, 'generation':generation, 'child_pid': child.pid, 'exit_code': child.returncode,
              'shutdown': 'forced' if forced else 'graceful' if stopping and child.returncode == 0 else 'failed'}
    path = root/'logs'/f'{component}-shutdown.json'
    temporary = path.with_suffix('.next')
    with temporary.open('w') as f:
        os.fchmod(f.fileno(), 0o600);json.dump(result, f)
    os.replace(temporary, path)
    return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['bundle', 'install', 'doctor', 'status', 'start', 'stop', '_inventory', '_seal', '_probe', '_run', *sorted(PILOT_ACTIONS)])
    parser.add_argument('--root', type=Path, default=DEFAULT)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--bundle-sha256')
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--profile', choices=['installation', 'local-review'], default='installation')
    parser.add_argument('--nativehouse', type=Path)
    parser.add_argument('--selection-sha256')
    parser.add_argument('--component', choices=COMPONENTS)
    parser.add_argument('--route',choices=['live','ready'],default='live')
    parser.add_argument('--runtime-port', type=int, default=8092)
    parser.add_argument('--service-port', type=int, default=8091)
    args = parser.parse_args()
    try:
        if args.action == 'bundle':
            require(args.wheelhouse and args.output, 'Supply wheelhouse and output')
            result = bundle(args.wheelhouse, args.output, profile_name=args.profile, nativehouse=args.nativehouse)
        elif args.action == 'install':
            require(args.bundle, 'Supply the offline bundle')
            result = install(args.bundle, args.root, args.bundle_sha256, args.runtime_port, args.service_port)
        elif args.action in ('_inventory','_seal'):
            result = inventory(args.root,seal=args.action=='_seal')
        elif args.action == '_probe':
            result = probe_body(args.runtime_port,args.route,args.component)
        elif args.action == '_run':
            require(args.component, 'Supply component')
            result = supervise(args.root, args.component)
        else:
            root = private(args.root)
            with (root/'operation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.action in PILOT_ACTIONS:
                    require(args.selection_sha256 is not None, 'Supply the reviewed pilot selection pin')
                    result = pilot_action(root, args.action, args.selection_sha256)
                else:
                    if args.action == 'start' and config(root)['mode'] == 'local_review':
                        require(args.selection_sha256 is not None, 'Supply the reviewed pilot selection pin')
                        pilot_inputs(root, args.selection_sha256)
                    result = {'doctor': doctor, 'status': doctor, 'start': start, 'stop': stop}[args.action](root)
        print(json.dumps(result, sort_keys=True));return (result['exit_code'] or (0 if result['shutdown']=='graceful' else 2)) if args.action=='_run' else 0
    except (InstallError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        message = str(exc) if isinstance(exc, InstallError) else 'Mac setup rejected; check prerequisites, private configuration and installation logs.'
        print(json.dumps({'ok': False, 'error': message}));return 2


if __name__ == '__main__':
    raise SystemExit(main())
