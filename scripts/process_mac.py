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
WORKSPACE_ROLES = {'wallet-command': 'castalia-wallet-service',
                   'owner-setup': 'castalia-wallet-workspace-setup',
                   'context-verifier': 'process-runtime-secs-check'}
MAX_NATIVE_BYTES = 64 * 1024 * 1024


def native_rows(profile):
    if profile.get('schema') == 'hub.process-macos-release.v1':
        require('native' not in profile and 'native_profile' not in profile,
                'Native payloads require an explicit local-review release')
        return []
    require(set(profile) == {'schema', 'platform', 'machine', 'python', 'components',
                            'wheels', 'native_profile', 'native'}
            and profile['schema'] == 'hub.process-macos-release.v2'
            and profile['native_profile'] in {'local-review-v1', 'workspace-v1'},
            'Unsupported Mac release profile')
    roles = WORKSPACE_ROLES if profile['native_profile'] == 'workspace-v1' else NATIVE_ROLES
    rows = profile['native']
    require(type(rows) is list and len(rows) == len(roles),
            'Incomplete native payload closure')
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == {'role', 'filename', 'bytes', 'sha256',
                                                  'source_commit', 'mode'},
                'Invalid native payload record')
        role = row['role']
        require(type(role) is str and role in roles and role not in seen
                and row['filename'] == roles[role]
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
    require(profile_name in {'installation', 'local-review', 'workspace'}, 'Unsupported bundle profile')
    profile = load(ROOT/'config'/('process-macos-release.v1.json' if profile_name == 'installation'
                                 else 'process-macos-workspace-release.v1.json' if profile_name == 'workspace'
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
    if profile_name == 'workspace':
        launcher = launcher.replace('install --bundle', 'install --root "$HOME/Library/Application Support/Zenith/Process-Workspace" --runtime-port 8292 --service-port 8291 --bundle')
    elif native:
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


def installed(root, *, own_upgrade=False):
    root = private(root)
    require(own_upgrade or (not (root/'logs/workspace-software-upgrade.json').exists() and not (root/'logs/workspace-software-upgrade.json').is_symlink()),
            'Workspace software upgrade requires reconciliation; retained backups are never rolled back automatically')
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
    mode = {'local-review-v1': 'local_review', 'workspace-v1': 'workspace'}.get(profile.get('native_profile'), 'installation_only')
    if mode == 'workspace':
        require(root.name == 'Process-Workspace' and runtime_port == 8292 and service_port == 8291,
                'Workspace requires a separate Process-Workspace installation on ports 8292/8291')
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
    if mode == 'workspace':
        workspace_prepare_plan(root)
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
            and value['schema'] == 'hub.process-macos-config.v1' and value['mode'] in {'installation_only', 'local_review', 'workspace'}
            and all(type(value[k]) is int and 1024 <= value[k] <= 65535 for k in ('runtime_port', 'service_port'))
            and value['runtime_port'] != value['service_port'], 'Invalid local installation configuration')
    if value['mode'] == 'local_review':
        require(load(root/'profile.json').get('native_profile') == 'local-review-v1'
                and root.name == 'Process-Pilot'
                and value['runtime_port'] == 8192 and value['service_port'] == 8191,
                'Local-review mode requires its isolated pinned release and ports')
    if value['mode'] == 'workspace':
        require(load(root/'profile.json').get('native_profile') == 'workspace-v1'
                and root.name == 'Process-Workspace' and value['runtime_port'] == 8292
                and value['service_port'] == 8291, 'Workspace target differs from isolated release')
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


# Generic authoring has no SOP, run, executor or provider activation.
WORKSPACE_ACTIONS = {'workspace-status', 'workspace-prepare', 'workspace-setup', 'workspace-start',
                     'workspace-stop', 'workspace-command', 'workspace-renew-prepare', 'workspace-renew', 'workspace-renew-resume'}
WORKSPACE_CONFIGS = {'command-authority.json', 'wallet-command.json',
                     'runtime-policy.json', 'runtime-receiver.json'}


def workspace_profile(root):
    require(config(root)['mode'] == 'workspace', 'This action requires the isolated workspace')
    return load(root/'profile.json')


def workspace_prepare_plan(root, expected=None):
    profile = workspace_profile(root)
    require(not (root/'authority').exists() and not (root/'authority').is_symlink(),
            'Existing native custody is retained; preparation is create-only')
    plan = root/'config/workspace-setup-plan.json'
    previous = bounded_private(plan, 16384) if plan.exists() else None
    require(not plan.is_symlink(), 'Workspace proposal cannot be a symlink')
    current = hashlib.sha256(previous).hexdigest() if previous is not None else None
    require(current == expected, 'Workspace proposal changed after review')
    if previous is not None:
        history = root/'config'/('workspace-setup-plan-history-'+current+'.json')
        if history.exists():
            require(bounded_private(history, 16384) == previous, 'Workspace proposal history changed')
        else:
            with history.open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600);stream.write(previous);stream.flush();os.fsync(stream.fileno())
    rows = {r['role']: r for r in native_rows(profile)}
    at = int(time.time())
    value = {'schema': 'castalia.wallet-workspace-setup.v1', 'workspace_root': str(root),
             'not_before': str(at-5), 'not_after': str(at+3590),
             'identity_not_after': str(at-5+365*86400),
             'wallet_command_sha256': rows['wallet-command']['sha256'],
             'verifier_sha256': rows['context-verifier']['sha256']}
    temporary = root/'config'/('workspace-proposal-'+uuid.uuid4().hex+'.next')
    save(temporary, value)
    with temporary.open('rb') as stream:
        os.fsync(stream.fileno())
    proposed_sha256 = hashlib.sha256(bounded_private(temporary, 16384)).hexdigest()
    os.replace(temporary, plan)
    directory = os.open(plan.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {'schema': 'hub.workspace-proposal.v1', 'plan_sha256': proposed_sha256,
            'not_before': value['not_before'], 'not_after': value['not_after'],
            'identity_not_after': value['identity_not_after'], 'authority_created': False, 'process_created': False}


WORKSPACE_SELECTION = 'config/workspace-native-selection.json'
WORKSPACE_RENEW_PLAN = 'config/workspace-renewal-plan.json'
WORKSPACE_RENEW_HOLD = 'logs/workspace-renewal.json'
WORKSPACE_UPGRADE_HOLD = 'logs/workspace-software-upgrade.json'


def workspace_sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def workspace_store(path, raw, *, replace=False):
    require(type(raw) is bytes and len(raw) <= 65536, 'Workspace metadata exceeds its bound')
    if path.exists() or path.is_symlink():
        previous = bounded_private(path)
        if not replace:
            require(previous == raw, 'Retained workspace metadata changed')
            return
    temporary = path.parent/('.workspace-'+uuid.uuid4().hex+'.next')
    with temporary.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
    if not replace:
        require(not path.exists() and not path.is_symlink(), 'Workspace metadata appeared during publication')
    os.replace(temporary, path)
    workspace_sync(path.parent)


def workspace_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def workspace_selected(root):
    selection = root/WORKSPACE_SELECTION
    if not selection.exists() and not selection.is_symlink():
        return root/'authority/COMPLETE.json', None
    value = closed_json(bounded_private(selection, 4096))
    require(type(value) is dict and set(value) == {'schema','native_generation','complete_sha256'}
            and value['schema'] == 'hub.workspace-native-selection.v1'
            and type(value['native_generation']) is int and 1 < value['native_generation'] <= 9007199254740991
            and type(value['complete_sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['complete_sha256']),
            'Invalid selected workspace native receipt')
    return root/'authority/renewals'/str(value['native_generation'])/'COMPLETE.json', value['complete_sha256']


def workspace_receipt(root, path, expected=None):
    raw = bounded_private(path)
    require(expected is None or hashlib.sha256(raw).hexdigest() == expected, 'Selected workspace receipt changed')
    receipt = closed_json(raw)
    require(type(receipt) is dict and receipt.get('root') == str(root)
            and receipt.get('activation') is False and type(receipt.get('configuration_hashes')) is dict
            and set(receipt['configuration_hashes']) == WORKSPACE_CONFIGS,
            'Workspace native receipt differs from selected owner composition')
    if path == root/'authority/COMPLETE.json':
        require(receipt.get('schema') == 'castalia.wallet-workspace-setup-complete.v1'
                and receipt.get('plan_sha256') == hashlib.sha256(bounded_private(root/'config/workspace-setup-plan.json',16384)).hexdigest(),
                'Workspace native setup receipt differs from selected owner plan')
    else:
        generation = receipt.get('native_generation')
        require(type(generation) is int and 1 < generation <= 9007199254740991
                and path == root/'authority/renewals'/str(generation)/'COMPLETE.json'
                and receipt.get('schema') == 'castalia.wallet-workspace-renewal-complete.v1'
                and type(receipt.get('plan_sha256')) is str and re.fullmatch('[a-f0-9]{64}', receipt['plan_sha256']),
                'Invalid workspace renewal receipt')
        plan_raw = bounded_private(root/'config'/('workspace-renewal-plan-history-'+receipt['plan_sha256']+'.json'),16384)
        require(hashlib.sha256(plan_raw).hexdigest() == receipt['plan_sha256'], 'Renewal plan history changed')
        plan = closed_json(plan_raw)
        require(receipt.get('previous_complete') == plan.get('previous_complete'), 'Renewal predecessor changed')
    for name, digest in receipt['configuration_hashes'].items():
        require(type(digest) is str and re.fullmatch('[a-f0-9]{64}', digest)
                and hashlib.sha256(bounded_private(path.parent/'config'/name)).hexdigest() == digest,
                'Workspace native configuration changed')
    return receipt


def workspace_config_directory(root, receipt):
    return root/'authority/renewals'/str(receipt['native_generation'])/'config' if receipt.get('schema') == 'castalia.wallet-workspace-renewal-complete.v1' else root/'authority/config'


def workspace_holds(root, *, own_renewal=False, resume_generation=None):
    paths = [root/'logs/workspace-operation.json',root/'data/frank-workspace-cleanup.json',root/WORKSPACE_UPGRADE_HOLD]
    if not own_renewal:
        paths.append(root/WORKSPACE_RENEW_HOLD)
    paths.extend(root/'authority/native'/name for name in ['wallet-transition.json','wallet-transition','.wallet-transition.tmp'])
    if resume_generation is not None:
        paths = [p for p in paths if p.parent != root/'authority/native']
    if any(p.exists() or p.is_symlink() for p in paths):
        return True
    renewals = root/'authority/renewals'
    if renewals.is_symlink():
        return True
    if renewals.exists():
        for index,p in enumerate(renewals.iterdir()):
            if index >= 64:
                return True
            if p.is_symlink() or not p.is_dir() or not re.fullmatch('[1-9][0-9]{0,15}',p.name):
                return True
            if int(p.name) != resume_generation and not (p/'COMPLETE.json').is_file():
                return True
    return False


def workspace_stopped(root):
    require(not any(launch_status(root,name) for name in COMPONENTS), 'Stop workspace before permission renewal or software upgrade')
    check_ports(config(root))


def workspace_renewal_plan(root, selection):
    raw = bounded_private(root/WORKSPACE_RENEW_PLAN,16384)
    require(type(selection) is str and re.fullmatch('[a-f0-9]{64}',selection)
            and hashlib.sha256(raw).hexdigest() == selection, 'Workspace renewal plan changed after review')
    value = closed_json(raw)
    require(type(value) is dict and set(value) == {'schema','workspace_root','previous_complete','not_before','not_after','wallet_command_sha256','verifier_sha256'}
            and value['schema'] == 'castalia.wallet-workspace-renewal.v1' and value['workspace_root'] == str(root),
            'Invalid workspace renewal proposal')
    previous = value['previous_complete']
    require(type(previous) is dict and set(previous) == {'path','sha256'}
            and type(previous['path']) is str and type(previous['sha256']) is str
            and re.fullmatch('[a-f0-9]{64}',previous['sha256']), 'Invalid renewal predecessor')
    for key in ['not_before','not_after']:
        require(type(value[key]) is str and re.fullmatch('[1-9][0-9]{0,15}',value[key])
                and int(value[key]) <= 9007199254740991, 'Invalid renewal time')
    start,end = int(value['not_before']),int(value['not_after'])
    require(start < end and end-start <= 3600, 'Renewal permission window exceeds one hour')
    rows = {row['role']:row for row in native_rows(workspace_profile(root))}
    require(value['wallet_command_sha256'] == rows['wallet-command']['sha256']
            and value['verifier_sha256'] == rows['context-verifier']['sha256'], 'Renewal executable pins changed')
    return raw,value


def workspace_prepare_renewal(root, expected=None):
    require(not workspace_holds(root), 'Workspace renewal or cleanup must be reconciled')
    workspace_stopped(root)
    receipt = workspace_ready(root)
    previous,pin = workspace_selected(root)
    previous_raw = bounded_private(previous)
    require(pin is None or hashlib.sha256(previous_raw).hexdigest() == pin, 'Selected workspace receipt changed')
    plan = root/WORKSPACE_RENEW_PLAN
    old = bounded_private(plan,16384) if plan.exists() or plan.is_symlink() else None
    require((hashlib.sha256(old).hexdigest() if old is not None else None) == expected, 'Workspace renewal proposal changed after review')
    if old is not None:
        workspace_store(root/'config'/('workspace-renewal-proposal-history-'+expected+'.json'),old)
    at = int(time.time());start=at-5;end=at+3590
    identity_end = receipt.get('identity_not_after',receipt.get('holder',{}).get('not_after'))
    require(type(identity_end) is int and end <= identity_end, 'Machine identity cannot cover the proposed permission window; identity is retained')
    rows={row['role']:row for row in native_rows(workspace_profile(root))}
    value={'schema':'castalia.wallet-workspace-renewal.v1','workspace_root':str(root),
           'previous_complete':{'path':str(previous),'sha256':hashlib.sha256(previous_raw).hexdigest()},
           'not_before':str(start),'not_after':str(end),
           'wallet_command_sha256':rows['wallet-command']['sha256'],'verifier_sha256':rows['context-verifier']['sha256']}
    raw=workspace_bytes(value);workspace_store(plan,raw,replace=old is not None)
    return {'schema':'hub.workspace-renewal-proposal.v1','plan_sha256':hashlib.sha256(raw).hexdigest(),
            'not_before':value['not_before'],'not_after':value['not_after'],'identity_not_after':str(identity_end),
            'same_identity':True,'authority_changed':False}


def workspace_renewal_identity(old,new):
    immutable=['schema','store_id','subject_id','purpose','application','key_generation','key_id','public_key_hex','public_key_sha256','created_at','key_created_at','not_before','not_after','state','revoked_at']
    a,b=old.get('holder'),new.get('holder')
    require(type(a) is dict and type(b) is dict and all(k in a and k in b and a[k] == b[k] for k in immutable)
            and a.get('state') == 'active' and type(a.get('state_revision')) is int
            and b.get('state_revision') == a['state_revision']+1
            and new.get('command_issuer') == old.get('command_issuer')
            and new.get('identity_not_after',b.get('not_after')) == old.get('identity_not_after',a.get('not_after'))
            and new.get('journal_capacity') == old.get('journal_capacity')
            and new.get('scope') == old.get('scope'), 'Renewal changed identity, issuer, scope or capacity')


def workspace_frank(root, action, master, pin, input_path=None, input_pin=None):
    argv=[root/'venv/bin/python','-I','-B','-m','frank_executor.workspace_cli',action,'--config',master,'--config-sha256',pin]
    if input_path is not None:
        argv += ['--input',input_path,'--input-sha256',input_pin]
    return workspace_run(argv,root)


def workspace_renew(root, selection, *, resume=False):
    workspace_stopped(root)
    raw,plan=workspace_renewal_plan(root,selection)
    history=root/'config'/('workspace-renewal-plan-history-'+selection+'.json')
    operation_dir=root/'logs'/('workspace-renewal-'+selection)
    marker=root/WORKSPACE_RENEW_HOLD
    if resume:
        operation=closed_json(bounded_private(marker))
        require(type(operation) is dict and operation.get('schema') == 'hub.workspace-renewal-operation.v1'
                and operation.get('plan_sha256') == selection, 'Resume requires the exact retained renewal')
        require(bounded_private(history,16384) == raw, 'Retained renewal plan changed')
    else:
        require(not workspace_holds(root), 'Workspace renewal or cleanup must be reconciled')
        old=workspace_ready(root);previous,pin=workspace_selected(root);previous_raw=bounded_private(previous)
        require(plan['previous_complete'] == {'path':str(previous),'sha256':hashlib.sha256(previous_raw).hexdigest()}, 'Renewal selects another predecessor')
        require(type(old.get('native_generation')) is int and 1 <= old['native_generation'] < 9007199254740991, 'Invalid native generation')
        master=bounded_private(root/'config/frank-workspace.json');admission=bounded_private(root/'config/workspace-admission.json')
        selected=bounded_private(root/WORKSPACE_SELECTION,4096) if (root/WORKSPACE_SELECTION).exists() else None
        if not operation_dir.exists():
            operation_dir.mkdir(mode=0o700);workspace_sync(operation_dir.parent)
        private(operation_dir)
        workspace_store(operation_dir/'previous-configuration.json',master)
        workspace_store(operation_dir/'previous-admission.json',admission)
        if selected is not None:workspace_store(operation_dir/'previous-selection.json',selected)
        workspace_store(history,raw)
        operation={'schema':'hub.workspace-renewal-operation.v1','plan_sha256':selection,
                   'previous_complete':plan['previous_complete'],'previous_configuration_sha256':hashlib.sha256(master).hexdigest(),
                   'previous_admission_sha256':hashlib.sha256(admission).hexdigest(),
                   'previous_selection_sha256':hashlib.sha256(selected).hexdigest() if selected is not None else None,
                   'successor_generation':old['native_generation']+1}
        workspace_store(marker,workspace_bytes(operation))
    generation=operation.get('successor_generation')
    require(type(generation) is int and 1 < generation <= 9007199254740991
            and operation.get('previous_complete') == plan['previous_complete'], 'Invalid retained renewal binding')
    require(not workspace_holds(root,own_renewal=True,resume_generation=generation if resume else None), 'Workspace cleanup or another native transition is unresolved')
    old_path=Path(plan['previous_complete']['path'])
    require(old_path == root/'authority/COMPLETE.json' or old_path == root/'authority/renewals'/str(generation-1)/'COMPLETE.json', 'Renewal predecessor path differs')
    old=workspace_receipt(root,old_path,plan['previous_complete']['sha256'])
    old_master=operation_dir/'previous-configuration.json';old_master_raw=bounded_private(old_master)
    require(hashlib.sha256(old_master_raw).hexdigest() == operation['previous_configuration_sha256']
            and closed_json(old_master_raw) == workspace_master(root,old), 'Retained old client composition changed')
    require(hashlib.sha256(bounded_private(operation_dir/'previous-admission.json')).hexdigest() == operation['previous_admission_sha256'], 'Retained admission changed')
    new_path=root/'authority/renewals'/str(generation)/'COMPLETE.json'
    completed=operation_dir/'COMPLETE.json'
    native_started=operation_dir/'native-started.json'
    try:
        # Readiness is a read-only no-pending-intent barrier; it never replays a command.
        try:
            readiness=workspace_frank(root,'check-renewal',old_master,operation['previous_configuration_sha256'])
            expected_readiness=operation['previous_configuration_sha256']
        except InstallError:
            require(resume and new_path.exists() and not (root/'logs/workspace-operation.json').exists()
                    and (operation_dir/'next-configuration.json').exists(), 'Resolve pending Frank commands before permission renewal')
            next_bytes=bounded_private(operation_dir/'next-configuration.json')
            require(bounded_private(root/'config/frank-workspace.json') == next_bytes, 'Retained successor client composition changed')
            expected_readiness=hashlib.sha256(next_bytes).hexdigest()
            readiness=workspace_frank(root,'check-renewal',root/'config/frank-workspace.json',expected_readiness)
        require(type(readiness) is dict and readiness.get('schema') == 'frank.workspace-renewal-readiness.v1'
                and readiness.get('configuration_sha256') == expected_readiness
                and readiness.get('journal') in {'absent','bound'}
                and readiness.get('ready') is True and readiness.get('pending_commands') == 0
                and readiness.get('cleanup_retained') is False, 'Frank renewal readiness is unresolved')
        workspace_store(operation_dir/('readiness-'+expected_readiness+'.json'),workspace_bytes(readiness))
        workspace_store(native_started,workspace_bytes({'plan_sha256':selection}))
        output=workspace_run([root/'native/castalia-wallet-workspace-setup','resume-renewal' if resume else 'renew','--plan',history,'--plan-sha256',selection],root,native_renewal=True)
        require(type(output) is dict and output.get('schema') == 'castalia.wallet-workspace-renewal-output.v1'
                and output.get('status') == 'configured_not_activated'
                and type(output.get('complete')) is dict and set(output['complete']) == {'path','sha256'}
                and output['complete']['path'] == str(new_path), 'Native renewal output differs from retained plan')
        new_pin=output['complete']['sha256'];new=workspace_receipt(root,new_path,new_pin)
        require(new.get('native_generation') == generation and new.get('plan_sha256') == selection
                and new.get('previous_complete') == plan['previous_complete']
                and new.get('not_before') == int(plan['not_before']) and new.get('not_after') == int(plan['not_after']), 'Native renewal binding changed')
        workspace_renewal_identity(old,new)
        workspace_receipt(root,old_path,plan['previous_complete']['sha256'])
        workspace_store(operation_dir/'native-result.json',workspace_bytes(output))
        next_master=workspace_bytes(workspace_master(root,new))
        next_admission=workspace_bytes({'schema':'runtime.new-run-admission.v1','enabled':False,'composition_digest':selection})
        next_selection=workspace_bytes({'schema':'hub.workspace-native-selection.v1','native_generation':generation,'complete_sha256':new_pin})
        workspace_store(operation_dir/'next-configuration.json',next_master)
        workspace_store(operation_dir/'next-admission.json',next_admission)
        workspace_store(operation_dir/'next-selection.json',next_selection)
        current_master=bounded_private(root/'config/frank-workspace.json')
        require(current_master in (old_master_raw,next_master), 'Live client configuration changed during renewal')
        migration_sha=None
        if readiness['journal'] == 'absent':
            journal=root/'data/frank-workspace.db'
            require(not any(path.exists() or path.is_symlink() for path in [journal,Path(str(journal)+'-wal'),Path(str(journal)+'-shm'),Path(str(journal)+'-journal')]),
                    'Workspace journal appeared after absent-journal readiness')
            workspace_store(root/'config/frank-workspace.json',next_master,replace=True)
            result={'schema':'hub.workspace-binding-uninitialized.v1','journal':'absent',
                    'previous_configuration_sha256':operation['previous_configuration_sha256'],
                    'configuration_sha256':hashlib.sha256(next_master).hexdigest(),'commands_preserved':True}
        else:
            migration={'schema':'frank.workspace-migration.v1',
                       'previous_configuration':{'path':str(old_master),'sha256':operation['previous_configuration_sha256']},
                       'previous_complete':plan['previous_complete'],'renewed_complete':output['complete']}
            migration_raw=workspace_bytes(migration);migration_path=operation_dir/'migration.json';workspace_store(migration_path,migration_raw)
            migration_sha=hashlib.sha256(migration_raw).hexdigest()
            workspace_store(root/'config/frank-workspace.json',next_master,replace=True)
            result=workspace_frank(root,'migrate-binding',root/'config/frank-workspace.json',hashlib.sha256(next_master).hexdigest(),migration_path,migration_sha)
            require(type(result) is dict and result.get('schema') == 'frank.workspace-migration-result.v1'
                    and result.get('previous_configuration_sha256') == operation['previous_configuration_sha256']
                    and result.get('configuration_sha256') == hashlib.sha256(next_master).hexdigest()
                    and result.get('migration_sha256') == migration_sha
                    and result.get('previous_complete_sha256') == plan['previous_complete']['sha256']
                    and result.get('renewed_complete_sha256') == new_pin and result.get('commands_preserved') is True,
                    'Frank migration result differs from retained composition')
        workspace_store(operation_dir/'migration-result.json',workspace_bytes({k:v for k,v in result.items() if k != 'duplicate'}))
        current_admission=bounded_private(root/'config/workspace-admission.json')
        require(current_admission in (bounded_private(operation_dir/'previous-admission.json'),next_admission), 'Run admission changed during renewal')
        workspace_store(root/'config/workspace-admission.json',next_admission,replace=True)
        selected_path=root/WORKSPACE_SELECTION
        selected_raw=bounded_private(selected_path,4096) if selected_path.exists() else None
        require(selected_raw == next_selection or (hashlib.sha256(selected_raw).hexdigest() if selected_raw is not None else None) == operation['previous_selection_sha256'], 'Selected native receipt changed during renewal')
        workspace_store(selected_path,next_selection,replace=selected_raw is not None)
        workspace_ready(root,own_renewal=True)
        receipt={'schema':'hub.workspace-renewal-complete.v1','plan_sha256':selection,
                 'previous_complete':plan['previous_complete'],'complete':output['complete'],
                 'configuration_sha256':hashlib.sha256(next_master).hexdigest(),
                 'migration_sha256':migration_sha,'journal':readiness['journal'],'commands_preserved':True,'identity_preserved':True,'execution_enabled':False}
        workspace_store(completed,workspace_bytes(receipt))
        require(closed_json(bounded_private(marker)) == operation, 'Renewal hold changed')
        marker.unlink();workspace_sync(marker.parent)
        return receipt
    except Exception as failure:
        # A known preflight denial before Native was invoked can release only our
        # own hold. Once Native could have run, preserve every recovery artifact.
        release = not native_started.exists() or (getattr(failure,'native_preflight_denied',False) and not new_path.parent.exists() and not new_path.parent.is_symlink() and not workspace_holds(root,own_renewal=True))
        if release and not (root/'logs/workspace-operation.json').exists() and not (root/'data/frank-workspace-cleanup.json').exists():
            require(closed_json(bounded_private(marker)) == operation, 'Renewal hold changed')
            marker.unlink();workspace_sync(marker.parent)
        raise


def workspace_complete(root):
    workspace_profile(root)
    selected,pin = workspace_selected(root)
    return workspace_receipt(root,selected,pin)


def workspace_master(root, receipt):
    row = next(r for r in native_rows(load(root/'profile.json')) if r['role'] == 'wallet-command')
    return {'schema': 'frank.workspace.v1',
            'runtime_endpoint': 'http://127.0.0.1:'+str(config(root)['runtime_port']),
            'journal': str(root/'data/frank-workspace.db'),
            'wallet': {'executable': str(root/'native'/row['filename']),
                       'executable_sha256': row['sha256'],
                       'configuration': str(workspace_config_directory(root,receipt)/'wallet-command.json'),
                       'configuration_sha256': receipt['configuration_hashes']['wallet-command.json']}}


def workspace_ready(root, *, own_renewal=False):
    require(not workspace_holds(root,own_renewal=own_renewal), 'Workspace renewal, software upgrade or cleanup is unresolved')
    receipt = workspace_complete(root)
    require(closed_json(bounded_private(root/'config/frank-workspace.json')) == workspace_master(root, receipt),
            'Workspace client configuration differs from native setup')
    admission = closed_json(bounded_private(root/'config/workspace-admission.json'))
    require(admission == {'schema': 'runtime.new-run-admission.v1', 'enabled': False,
                          'composition_digest': receipt['plan_sha256']},
            'Workspace authoring requires closed run admission')
    return receipt


def workspace_run(argv, root, *, native_renewal=False):
    if native_renewal:
        operation = closed_json(bounded_private(root/WORKSPACE_RENEW_HOLD))
        require(len(argv) == 6 and argv[0] == root/'native/castalia-wallet-workspace-setup'
                and argv[1] in {'renew','resume-renewal'} and argv[2] == '--plan'
                and argv[4] == '--plan-sha256' and operation.get('schema') == 'hub.workspace-renewal-operation.v1'
                and operation.get('plan_sha256') == argv[5], 'Invalid native renewal ownership mode')
    marker = root/'logs/workspace-operation.json'
    cleanup = root/'data/frank-workspace-cleanup.json'
    require(not marker.exists() and not marker.is_symlink() and not cleanup.exists()
            and not cleanup.is_symlink(), 'Workspace command cleanup is unresolved')
    operation = {'schema': 'hub.workspace-operation.v1', 'id': uuid.uuid4().hex}
    save(marker, operation)
    with marker.open('rb') as stream:
        os.fsync(stream.fileno())
    directory = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    child = None
    resolved = False
    with tempfile.TemporaryFile(dir=root/'logs') as out, tempfile.TemporaryFile(dir=root/'logs') as err:
        try:
            child = subprocess.Popen([str(x) for x in argv], cwd=root, env=environment(root),
                                     stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            deadline = time.monotonic()+90
            reason = None
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    reason = 'Workspace command timed out'
                if os.fstat(out.fileno()).st_size+os.fstat(err.fileno()).st_size > 262144:
                    reason = 'Workspace output exceeded bound'
                if reason:
                    child.send_signal(signal.SIGINT)
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.kill();child.wait(timeout=5)
                        raise InstallError(reason+'; cleanup must be reconciled')
                    break
                time.sleep(.02)
            resolved = (child.returncode in (0, 2, 3) or (native_renewal and child.returncode == 4 and reason is None)) and not cleanup.exists() and not cleanup.is_symlink()
            out.seek(0);err.seek(0)
            raw, diagnostic = out.read(262145), err.read(262145)
            require(len(raw)+len(diagnostic) <= 262144, 'Workspace output exceeded bound')
            if reason or child.returncode:
                for suffix, data in [('stdout', raw), ('stderr', diagnostic)]:
                    file = root/'logs'/('workspace-'+operation['id']+'.'+suffix+'.log')
                    with file.open('xb') as stream:
                        os.fchmod(stream.fileno(), 0o600);stream.write(data)
                failure = InstallError((reason or 'Workspace command denied or outcome unknown')+'; private logs retained')
                failure.native_preflight_denied = native_renewal and child.returncode == 2 and reason is None and not cleanup.exists() and not cleanup.is_symlink()
                raise failure
            return closed_json(raw)
        finally:
            if child is None or resolved:
                require(closed_json(bounded_private(marker)) == operation, 'Workspace operation marker changed')
                marker.unlink()
                directory = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)


def workspace_upgrade_policy(old,new):
    require(old.get('native_profile') == new.get('native_profile') == 'workspace-v1'
            and all(old.get(k) == new.get(k) for k in ['schema','platform','machine','python']),
            'Software upgrade requires the same isolated workspace release profile')
    a,b={r['role']:r for r in native_rows(old)},{r['role']:r for r in native_rows(new)}
    require(all(a[k] == b[k] for k in ['wallet-command','context-verifier']),
            'Software upgrade cannot change Wallet command or verifier bytes or provenance')
    a,b={r['name']:r for r in old['wheels']},{r['name']:r for r in new['wheels']}
    require(set(a) == set(b) and len(a) == 15 and 'frank-executor' in a
            and all(a[k] == b[k] for k in a if k != 'frank-executor'),
            'Software upgrade can change only the Frank wheel')
    a,b=old.get('components'),new.get('components')
    require(type(a) is dict and type(b) is dict and set(a) == set(b) and 'frank' in a
            and all(a[k] == b[k] for k in a if k != 'frank')
            and a['frank'].get('package') == b['frank'].get('package') == 'frank-executor',
            'Software upgrade cannot change Runtime, Service or component closure')


def workspace_software_upgrade(source,root,expected):
    # Invoke the exact new bundle controller. Existing installed pins are
    # verified before the first mutation; no native owner action is run here.
    require(not (root/WORKSPACE_UPGRADE_HOLD).exists() and not (root/WORKSPACE_UPGRADE_HOLD).is_symlink(),
            'Workspace software upgrade requires reconciliation; retained backups are never rolled back automatically')
    new=check_bundle(source,expected);host(new)
    require(sha(__file__) == load(Path(source)/'bundle.json')['files']['process_mac.py'],
            'Use the controller delivered in this exact upgrade bundle')
    previous=installed(root);old=workspace_profile(root)
    workspace_upgrade_policy(old,new)
    workspace_stopped(root)
    require(not workspace_holds(root), 'Reconcile workspace renewal or cleanup before software upgrade')
    current=workspace_ready(root)
    selected,_=workspace_selected(root)
    authority_pin=hashlib.sha256(bounded_private(selected)).hexdigest()
    mutable=['config/frank-workspace.json','config/workspace-admission.json','config/local.json','config/frank.json']
    if (root/WORKSPACE_SELECTION).exists():mutable.append(WORKSPACE_SELECTION)
    preserved={name:hashlib.sha256(bounded_private(root/name)).hexdigest() for name in mutable}
    operation_id=uuid.uuid4().hex
    parent=root/'software-upgrades'
    if not parent.exists():
        parent.mkdir(mode=0o700);workspace_sync(root)
    private(parent)
    operation_dir=parent/operation_id;operation_dir.mkdir(mode=0o700);workspace_sync(parent)
    candidate=operation_dir/'candidate';candidate.mkdir(mode=0o700)
    old_dir=operation_dir/'previous';old_dir.mkdir(mode=0o700);workspace_sync(operation_dir)
    manifest=load(Path(source)/'bundle.json')
    for name in ['bundle.json',*manifest['files']]:
        target=candidate/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        shutil.copyfile(Path(source)/name,target)
        target.chmod(next((r['mode'] for r in native_rows(new) if name == 'native/'+r['filename']),0o600))
        with target.open('rb') as stream:os.fsync(stream.fileno())
        workspace_sync(target.parent)
    check_bundle(candidate,expected)
    workspace_sync(candidate)
    operation={'schema':'hub.workspace-software-upgrade-operation.v1','id':operation_id,
               'previous_installed_sha256':hashlib.sha256(bounded_private(root/'installed.json')).hexdigest(),
               'previous_bundle_sha256':previous['bundle_sha256'],'bundle_sha256':expected,
               'selected_complete':{'path':str(selected),'sha256':authority_pin},'preserved_configuration':preserved}
    marker=root/WORKSPACE_UPGRADE_HOLD
    workspace_store(marker,workspace_bytes(operation))
    # Once the hold is durable, any interruption leaves a blocked installation
    # and every previous software object under its original basename.
    for name in ['bundle.json','profile.json','process_mac.py','control.py','installed-files.json','installed.json','wheels','native','venv']:
        path=root/name
        require(not path.is_symlink() and path.exists(), 'Installed software object changed before backup')
        os.rename(path,old_dir/name)
        workspace_sync(root);workspace_sync(old_dir)
    for name in ['bundle.json',*manifest['files']]:
        target=root/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        shutil.copyfile(candidate/name,target)
        target.chmod(next((r['mode'] for r in native_rows(new) if name == 'native/'+r['filename']),0o600))
        with target.open('rb') as stream:os.fsync(stream.fileno())
        workspace_sync(target.parent)
    shutil.copyfile(root/'process_mac.py',root/'control.py');(root/'control.py').chmod(0o600)
    # Create at its final path so interpreter and generated script paths do not
    # retain a temporary staging pathname. All installation is offline.
    venv.EnvBuilder(with_pip=True,symlinks=True).create(root/'venv')
    python=root/'venv/bin/python'
    wheels=[root/'wheels'/r['filename'] for r in new['wheels']]
    run([python,'-I','-B','-m','pip','install','--isolated','--no-index','--no-deps','--no-compile','--no-cache-dir','--disable-pip-version-check',*wheels],root,timeout=180)
    run([python,'-I','-B','-m','pip','check'],root)
    run([python,'-I','-S','-B',root/'control.py','_seal','--root',root],root)
    run([python,'-I','-S','-B',root/'control.py','_inventory','--root',root],root)
    # Flush owned installed files/directories before the final installed receipt.
    # Interpreter links refer to the unchanged pinned base Python and are skipped.
    for directory,_,names in os.walk(root/'venv',topdown=False,followlinks=False):
        for name in names:
            path=Path(directory)/name
            if not path.is_symlink():
                with path.open('rb') as stream:os.fsync(stream.fileno())
        workspace_sync(directory)
    for path in [root/'control.py',root/'installed-files.json']:
        with path.open('rb') as stream:os.fsync(stream.fileno())
    workspace_sync(root)
    check_bundle(root,expected)
    require(hashlib.sha256(bounded_private(selected)).hexdigest() == authority_pin
            and all(hashlib.sha256(bounded_private(root/name)).hexdigest() == pin for name,pin in preserved.items())
            and workspace_complete(root) == current, 'Workspace authority or configuration changed during software upgrade')
    next_receipt={**previous,'bundle_sha256':expected,'control_sha256':sha(root/'control.py'),
                  'installed_files_sha256':sha(root/'installed-files.json'),'authority_qualified':False}
    next_raw=workspace_bytes(next_receipt)
    receipt={'schema':'hub.workspace-software-upgrade-complete.v1','id':operation_id,
             'previous_installed_sha256':operation['previous_installed_sha256'],
             'installed_sha256':hashlib.sha256(next_raw).hexdigest(),'bundle_sha256':expected,
             'previous_bundle_sha256':previous['bundle_sha256'],'preserved_configuration':preserved,
             'selected_complete':operation['selected_complete'],'data_preserved':True,'authority_preserved':True,
             'authority_qualified':False,'external_effects':False}
    workspace_store(operation_dir/'COMPLETE.json',workspace_bytes(receipt))
    workspace_store(root/'installed.json',next_raw)
    installed(root,own_upgrade=True)
    require(closed_json(bounded_private(marker)) == operation, 'Software upgrade hold changed')
    marker.unlink();workspace_sync(marker.parent)
    return receipt


def workspace_permission(receipt):
    # This is an observation of the exact selected public receipt, never a grant.
    observed = int(time.time())
    result = {'state': 'unknown', 'not_before': None, 'not_after': None,
              'identity_not_after': None, 'observed_at': str(observed)}
    safe = lambda value: type(value) is int and 0 < value <= 9007199254740991
    start, end = receipt.get('not_before'), receipt.get('not_after')
    identity_end = receipt.get('identity_not_after')
    if not (safe(start) and safe(end) and start < end and end-start <= 3600
            and (identity_end is None or (safe(identity_end) and end <= identity_end))
            and 0 <= observed <= 9007199254740991):
        return result
    result.update(state='not_yet_valid' if observed < start else 'active' if observed < end else 'expired',
                  not_before=str(start), not_after=str(end),
                  identity_not_after=str(identity_end) if identity_end is not None else None)
    return result


def workspace_report(root):
    workspace_profile(root)
    pending = workspace_holds(root)
    result = {'schema': 'hub.workspace-status.v1', 'setup': 'not_selected',
              'authority': 'pending', 'execution_enabled': False,
              'provider_enabled': False, 'workflow_selected': False,
              'cleanup_unresolved': pending}
    if (root/'authority').exists():
        try:
            receipt = workspace_ready(root)
            result.update(setup='configured', authority='configured_native_unqualified',
                          permission=workspace_permission(receipt))
        except (InstallError, OSError, ValueError, KeyError, TypeError):
            result.update(setup='partial', authority='unavailable')
    elif (root/'config/workspace-setup-plan.json').exists():
        result['setup'] = 'absent'
    marker = root/WORKSPACE_RENEW_HOLD
    result['renewal'] = {'state':'none','resumable':False}
    if marker.exists() or marker.is_symlink():
        result['renewal'] = {'state':'recovery_required','resumable':False}
        try:
            operation = closed_json(bounded_private(marker))
            pin = operation.get('plan_sha256')
            raw,plan = workspace_renewal_plan(root,pin)
            require(operation.get('schema') == 'hub.workspace-renewal-operation.v1'
                    and operation.get('previous_complete') == plan['previous_complete']
                    and bounded_private(root/'config'/('workspace-renewal-plan-history-'+pin+'.json'),16384) == raw,
                    'Retained renewal differs')
            result['renewal'].update(resumable=not (root/'logs/workspace-operation.json').exists()
                and not (root/'data/frank-workspace-cleanup.json').exists(),plan_sha256=pin)
        except (InstallError,OSError,ValueError,KeyError,TypeError):
            pass
    return result


def workspace_action(root, action, raw=None, expected=None, selection=None):
    installed(root)
    workspace_profile(root)
    if action == 'workspace-stop':
        return stop(root)
    if action == 'workspace-status':
        return doctor(root)
    if action == 'workspace-start':
        return start(root)
    if action == 'workspace-renew-prepare':
        return workspace_prepare_renewal(root,selection)
    if action in {'workspace-renew','workspace-renew-resume'}:
        return workspace_renew(root,selection,resume=action=='workspace-renew-resume')
    if action == 'workspace-prepare':
        require(not any(launch_status(root, name) for name in COMPONENTS), 'Stop workspace before preparing setup')
        return workspace_prepare_plan(root, selection)
    if action == 'workspace-setup':
        require(not (root/'authority').exists() and not (root/'authority').is_symlink(),
                'Existing native custody is retained; setup is create-only')
        require(not any(launch_status(root, name) for name in COMPONENTS), 'Stop workspace before setup')
        plan = root/'config/workspace-setup-plan.json'
        plan_bytes = bounded_private(plan, 16384)
        require(type(selection) is str and re.fullmatch('[a-f0-9]{64}', selection)
                and hashlib.sha256(plan_bytes).hexdigest() == selection, 'Workspace setup plan changed after review')
        value = closed_json(plan_bytes)
        require(type(value) is dict and value.get('schema') == 'castalia.wallet-workspace-setup.v1'
                and value.get('workspace_root') == str(root), 'Workspace setup selects another root')
        result = workspace_run([root/'native/castalia-wallet-workspace-setup', 'create',
                               '--plan', plan, '--plan-sha256', selection], root)
        receipt = workspace_complete(root)
        require(receipt['plan_sha256'] == selection, 'Workspace receipt differs from reviewed plan')
        # Creation never publishes a process or opens run admission.
        save(root/'config/frank-workspace.json', workspace_master(root, receipt))
        save(root/'config/workspace-admission.json', {'schema': 'runtime.new-run-admission.v1',
             'enabled': False, 'composition_digest': selection})
        return result
    require(action == 'workspace-command' and type(raw) is str
            and len(raw.encode()) <= 65536 and type(expected) is str
            and re.fullmatch('[a-f0-9]{64}', expected)
            and hashlib.sha256(raw.encode()).hexdigest() == expected, 'Workspace request pin differs')
    request = closed_json(raw.encode())
    require(type(request) is dict and set(request) == {'schema', 'action', 'payload', 'idempotency_key'}
            and request['schema'] == 'zenith.workspace-command.v1'
            and request['action'] in {'catalog', 'get', 'validate', 'draft-put', 'publish', 'resume-command'}
            and type(request['payload']) is dict and type(request['idempotency_key']) is str
            and re.fullmatch('[a-zA-Z0-9-]{16,128}', request['idempotency_key']), 'Invalid workspace request')
    receipt = workspace_ready(root)
    master = root/'config/frank-workspace.json'
    master_bytes = bounded_private(master)
    require(closed_json(master_bytes) == workspace_master(root, receipt),
            'Workspace client configuration differs from native setup')
    master_digest = hashlib.sha256(master_bytes).hexdigest()
    require(request['action'] == 'validate' or owns_listener(root, 'runtime'), 'Start workspace Runtime first')
    requests = root/'data/workspace-requests'
    if not requests.exists():
        requests.mkdir(mode=0o700)
    private(requests)
    bound = requests/(request['idempotency_key']+'.request.json')
    canonical_request = json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if bound.exists():
        require(bounded_private(bound) == canonical_request, 'Workspace idempotency identity changed action or payload')
    else:
        with bound.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600);stream.write(canonical_request);stream.flush();os.fsync(stream.fileno())
    selected = requests/(request['idempotency_key']+'.json')
    payload = json.dumps(request['payload'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if selected.exists():
        require(bounded_private(selected) == payload, 'Workspace idempotency identity changed payload')
    else:
        with selected.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600);stream.write(payload);stream.flush();os.fsync(stream.fileno())
    return workspace_run([root/'venv/bin/python', '-I', '-B', '-m', 'frank_executor.workspace_cli',
                          request['action'], '--config', master, '--config-sha256', master_digest,
                          '--input', selected, '--input-sha256', hashlib.sha256(payload).hexdigest()], root)


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
    if value['mode'] == 'workspace':
        result['workspace'] = workspace_report(root)
        result['authority'] = result['workspace']['authority']
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
    if value.get('mode') == 'workspace':
        workspace_ready(root)
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
    if value['mode'] == 'workspace' and component == 'runtime':
        receipt = workspace_ready(root)
        argv += ['--receiver-config', workspace_config_directory(root,receipt)/'runtime-receiver.json',
                 '--admission-policy', root/'config/workspace-admission.json']
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
    parser.add_argument('action', choices=['bundle', 'install', 'workspace-software-upgrade', 'doctor', 'status', 'start', 'stop', '_inventory', '_seal', '_probe', '_run', *sorted(PILOT_ACTIONS), *sorted(WORKSPACE_ACTIONS)])
    parser.add_argument('--root', type=Path, default=DEFAULT)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--bundle-sha256')
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--profile', choices=['installation', 'local-review', 'workspace'], default='installation')
    parser.add_argument('--nativehouse', type=Path)
    parser.add_argument('--selection-sha256')
    parser.add_argument('--request-stdin', action='store_true')
    parser.add_argument('--request-sha256')
    parser.add_argument('--component', choices=COMPONENTS)
    parser.add_argument('--route',choices=['live','ready'],default='live')
    parser.add_argument('--runtime-port', type=int, default=8092)
    parser.add_argument('--service-port', type=int, default=8091)
    args = parser.parse_args()
    try:
        request_raw = None
        if args.request_stdin:
            require(args.action == 'workspace-command', 'Standard input is only accepted for workspace commands')
            request_bytes = sys.stdin.buffer.read(65537)
            require(0 < len(request_bytes) <= 65536, 'Workspace input exceeds its bound')
            request_raw = request_bytes.decode('utf-8')
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
                if args.action == 'workspace-software-upgrade':
                    require(args.bundle and args.bundle_sha256, 'Supply the exact supported workspace upgrade bundle and digest')
                    result = workspace_software_upgrade(args.bundle,root,args.bundle_sha256)
                elif args.action in WORKSPACE_ACTIONS:
                    result = workspace_action(root, args.action, request_raw, args.request_sha256, args.selection_sha256)
                elif args.action in PILOT_ACTIONS:
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
