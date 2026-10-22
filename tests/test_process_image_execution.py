"""Offline host lifecycle tests; no Docker engine or container is contacted."""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys

import pytest

from scripts import process_image_execution as host


@pytest.fixture
def execution(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'MIN_FREE', 0)
    output = tmp_path / 'images'
    output.mkdir()
    return host.ImageExecution(output, 'never-a-real-docker', [])


def test_timeout_reaps_descendants_and_preserves_partial_streams(execution):
    program = """
import signal, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
def stop(number, frame):
    child.terminate()
    child.wait(timeout=1)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
print('partial stdout before timeout', flush=True)
print('partial stderr before timeout', file=sys.stderr, flush=True)
time.sleep(60)
"""
    with pytest.raises(host.ExecutionFailure, match='deadline'):
        execution.run('timeout', [sys.executable, '-I', '-B', '-c', program], timeout=1)
    row = execution.commands[0]
    assert row['group_absent'] and not host.group_exists(row['owned_pgid'])
    assert row['signals'] == [int(signal.SIGTERM)]
    assert b'partial stdout before timeout' in (execution.directory/'timeout/stdout').read_bytes()
    assert b'partial stderr before timeout' in (execution.directory/'timeout/stderr').read_bytes()
    retained = json.loads((execution.directory/'lifecycle.json').read_bytes())
    assert retained['first_failure']['phase'] == 'timeout'
    assert retained['commands'][0]['argv'] == [sys.executable, '-I', '-B', '-c', program]


def test_output_limit_keeps_partial_bytes_and_fails(execution, monkeypatch):
    monkeypatch.setattr(host, 'MAX_OUTPUT', 128)
    with pytest.raises(host.ExecutionFailure, match='output budget'):
        execution.run('overflow', [sys.executable, '-I', '-B', '-c',
            "import sys,time;sys.stdout.write('x'*4096);sys.stdout.flush();time.sleep(60)"], timeout=5)
    assert (execution.directory/'overflow/stdout').stat().st_size >= 4096
    assert execution.commands[0]['group_absent']


def container_fixture(execution, monkeypatch, *, collision=False, mismatch=None, run_error=False):
    calls = []
    state = {'present': collision}
    identifier = 'a' * 64
    def run(label, argv, **kwargs):
        calls.append(list(argv))
        if argv[1:3] == ['container', 'inspect']:
            identity = argv[-1]
            if not state['present']:
                return subprocess.CompletedProcess(argv, 1, b'[]\n',
                    ('Error response from daemon: No such container: ' + identity + '\n').encode())
            row = {'Id': identifier, 'Name': '/' + state.get('name', 'preexisting'),
                'Config': {'Image': state.get('image', 'unrelated'),
                           'Labels': {host.LABEL: state.get('token', 'unrelated')}}}
            if mismatch == 'label': row['Config']['Labels'][host.LABEL] = 'different'
            if mismatch == 'image': row['Config']['Image'] = 'sha256:' + '0' * 64
            if mismatch == 'name': row['Name'] = '/different'
            if mismatch == 'cid': row['Id'] = 'b' * 64
            return subprocess.CompletedProcess(argv, 0, json.dumps([row]).encode(), b'')
        if argv[1] == 'run':
            state.update(present=True, name=argv[argv.index('--name') + 1],
                image=argv[argv.index('--entrypoint') + 2], token=execution.token)
            Path(argv[argv.index('--cidfile') + 1]).write_text(identifier)
            if run_error:
                error = host.ExecutionFailure('original run failure')
                execution.fail(label, error)
                raise error
        elif argv[1:3] == ['container', 'stop']:
            assert argv[-1] == identifier
        elif argv[1:3] == ['container', 'rm']:
            assert argv[-1] == identifier
            state['present'] = False
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, b'{}\n', b'')
    monkeypatch.setattr(execution, 'run', run)
    return calls, state


def test_owned_container_cleanup_is_exact_and_verified(execution, monkeypatch):
    calls, state = container_fixture(execution, monkeypatch)
    execution.container('frank.smoke', 'sha256:' + '1' * 64, ['smoke.py'])
    assert not state['present'] and execution.containers[0]['absent']
    mutation = [c for c in calls if c[1:3] in (['container', 'stop'], ['container', 'rm'])]
    assert [c[-1] for c in mutation] == ['a' * 64, 'a' * 64]
    command = next(c for c in calls if c[1] == 'run')
    assert '--pull=never' in command and '--network=none' in command and '--read-only' in command
    assert command[command.index('--label') + 1] == host.LABEL + '=' + execution.token


def test_preexisting_name_is_never_adopted_or_cleaned(execution, monkeypatch):
    calls, state = container_fixture(execution, monkeypatch, collision=True)
    with pytest.raises(host.ExecutionFailure, match='collision'):
        execution.container('frank.smoke', 'sha256:' + '1' * 64, ['smoke.py'])
    assert state['present'] and execution.containers == []
    assert all(c[1:3] == ['container', 'inspect'] for c in calls)


@pytest.mark.parametrize('mismatch', ['label', 'image', 'name', 'cid'])
def test_wrong_identity_is_not_stopped_or_removed(execution, monkeypatch, mismatch):
    calls, state = container_fixture(execution, monkeypatch, mismatch=mismatch)
    with pytest.raises(host.ExecutionFailure, match='mismatching'):
        execution.container('frank.smoke', 'sha256:' + '1' * 64, ['smoke.py'])
    assert state['present'] and not execution.containers[0]['absent']
    assert not any(c[1:3] in (['container', 'stop'], ['container', 'rm']) for c in calls)
    with pytest.raises(host.ExecutionFailure, match='incomplete'):
        execution.finish()


def test_cleanup_failure_does_not_replace_original_failure(execution, monkeypatch):
    calls, state = container_fixture(execution, monkeypatch, mismatch='label', run_error=True)
    with pytest.raises(host.ExecutionFailure, match='original run failure'):
        execution.container('frank.smoke', 'sha256:' + '1' * 64, ['smoke.py'])
    assert execution.first_failure['detail'] == 'original run failure'
    assert execution.secondary_errors[-1]['phase'] == 'frank.smoke.daemon_cleanup'
    assert not any(c[1:3] in (['container', 'stop'], ['container', 'rm']) for c in calls)


def test_engine_eof_is_not_container_absence(execution, monkeypatch):
    calls = []
    def run(label, argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, b'', b'Error: EOF\n')
    monkeypatch.setattr(execution, 'run', run)
    with pytest.raises(host.ExecutionFailure, match='unproved'):
        execution.container('frank.smoke', 'sha256:' + '1' * 64, ['smoke.py'])
    assert execution.containers == []
    assert all(c[1:3] == ['container', 'inspect'] for c in calls)


def build_fixture(execution, monkeypatch, outcome):
    calls = []
    def run(label, argv, **kwargs):
        calls.append(list(argv))
        if 'ls' in argv:
            return subprocess.CompletedProcess(argv, 0, b'[]', b'')
        if 'inspect' in argv:
            row = execution.builds[-1]
            name = row['name'] if outcome != 'wrong-name' else 'unrelated'
            value = {'Name': name, 'Ref': 'ownedref', 'Status': 'completed',
                     'CompletedAt': '2026-10-16T12:00:00Z'}
            return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode(), b'')
        assert argv[1:3] == ['buildx', 'build']
        if outcome != 'unknown':
            Path(argv[argv.index('--metadata-file') + 1]).write_text(
                json.dumps({'buildx.build.ref': 'default/default/ownedref'}))
        return subprocess.CompletedProcess(argv, 0, b'build output', b'')
    monkeypatch.setattr(execution, 'run', run)
    monkeypatch.setattr(host.time, 'sleep', lambda seconds: None)
    return calls


def test_build_requires_correlated_terminal_history(execution, monkeypatch):
    calls = build_fixture(execution, monkeypatch, 'complete')
    execution.build('frank-1', ['never-a-real-docker', 'buildx', 'build', 'context'])
    assert execution.builds[0]['terminal_observed']
    command = next(c for c in calls if c[1:3] == ['buildx', 'build'])
    assert 'BUILDKIT_BUILD_NAME=' + execution.builds[0]['name'] in command
    assert '--metadata-file' in command
    assert not any('rm' in c or 'prune' in c for c in calls)


@pytest.mark.parametrize('outcome', ['unknown', 'wrong-name'])
def test_missing_or_mismatching_history_holds_publication(execution, monkeypatch, outcome):
    calls = build_fixture(execution, monkeypatch, outcome)
    with pytest.raises(host.ExecutionFailure, match='unproved'):
        execution.build('frank-1', ['never-a-real-docker', 'buildx', 'build', 'context'])
    assert not execution.builds[0]['terminal_observed']
    with pytest.raises(host.ExecutionFailure, match='incomplete'):
        execution.finish()
    assert not (execution.output/'image-manifest.json').exists()
    assert not any('rm' in c or 'prune' in c for c in calls)


def assert_exact_payload_inventory(output):
    receipt = output/'execution/payloads.json'
    value = json.loads(receipt.read_bytes())
    assert value['exclusions'] == ['execution/payloads.json', 'image-manifest.json']
    actual = {}
    for path in output.rglob('*'):
        relative = str(path.relative_to(output))
        if path.is_file() and relative not in value['exclusions']:
            info = path.stat()
            actual[relative] = {'path': relative, 'bytes': info.st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'mode': stat.S_IMODE(info.st_mode)}
    assert {row['path']: row for row in value['files']} == actual
    assert not (output/'image-manifest.json').exists()


@pytest.mark.parametrize('phase', ['write', 'rename'])
@pytest.mark.skipif(os.environ.get('SKIP_PROCESS_IMAGE_HOST_TESTS') == '1',
                    reason='requires the reviewed Darwin arm64 image-host environment')
def test_publication_failure_keeps_exact_final_payload_inventory(tmp_path, monkeypatch, phase):
    from scripts import process_component_images as product
    monkeypatch.setattr(product._execution, 'MIN_FREE', 0)
    hub = tmp_path/'synthetic-hub'
    # Only file/checkpoint and publication behavior is exercised. No Docker,
    # image construction, policy change, or real component operation occurs.
    for relative in ('scripts/process_component_images.py', 'scripts/process_image_execution.py',
            'scripts/bootstrap_process_image_tools.py', 'scripts/process_image_inventory.py',
            'scripts/process_image_smoke.py', 'config/process-component-images.v1.json',
            'config/process-image-host-tools.v1.json', 'config/process-image-host-tools.lock',
            'docker/process-components/Dockerfile'):
        path = hub/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic checkpoint')
    contexts = tmp_path/'synthetic-contexts'
    contexts.mkdir()
    (contexts/'contexts.json').write_text('{}')
    monkeypatch.setattr(product, 'HUB', hub)
    monkeypatch.setattr(product, 'validate_prepared', lambda *args: {'components': {}})
    monkeypatch.setattr(product, '_build_images', lambda *args: {'synthetic_artifact': True})
    output = tmp_path/'images'
    pending = output/'image-manifest.pending'
    if phase == 'write':
        original_open = Path.open
        class PartialWrite:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def write(self, raw):
                self.stream.write(raw[:7])
                self.stream.flush()
                raise OSError('synthetic publication write failure')
        def failed_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return PartialWrite(stream) if path == pending and args == ('xb',) else stream
        monkeypatch.setattr(Path, 'open', failed_open)
    else:
        original_replace = product.os.replace
        def failed_replace(source, destination):
            if Path(destination) == output/'image-manifest.json':
                raise OSError('synthetic publication rename failure')
            return original_replace(source, destination)
        monkeypatch.setattr(product.os, 'replace', failed_replace)
    with pytest.raises(OSError, match='synthetic publication ' + phase + ' failure'):
        product.build_images(contexts, 'synthetic', output, docker='never-a-real-docker')
    assert pending.is_file() and pending.stat().st_size > 0
    if phase == 'write':
        assert pending.stat().st_size == 7
    retained = json.loads((output/'execution/lifecycle.json').read_bytes())
    assert retained['first_failure']['phase'] == 'build_images'
    assert retained['first_failure']['error_type'] == 'OSError'
    assert_exact_payload_inventory(output)
    assert 'image-manifest.pending' in {
        row['path'] for row in json.loads((output/'execution/payloads.json').read_bytes())['files']}


def test_initial_input_drift_preserves_first_error_and_refreshes_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'MIN_FREE', 0)
    checkpoint = tmp_path/'checkpoint'
    checkpoint.write_bytes(b'before')
    output = tmp_path/'images'
    output.mkdir()
    execution = host.ImageExecution(output, 'never-a-real-docker', [checkpoint])
    checkpoint.write_bytes(b'after')
    with pytest.raises(host.ExecutionFailure, match='incomplete'):
        execution.finish()
    first = dict(execution.first_failure)
    assert first['phase'] == 'final_checkpoints' and first['detail'] == 'Input checkpoint drift'
    assert_exact_payload_inventory(output)
    # Model the product entrypoint recording the raised finalization error.
    execution.fail('build_images', host.ExecutionFailure('outer failure attribution'))
    with pytest.raises(host.ExecutionFailure, match='incomplete'):
        execution.finish()
    retained = json.loads((output/'execution/lifecycle.json').read_bytes())
    assert retained['first_failure'] == first
    assert retained['secondary_errors'][-1]['phase'] == 'build_images'
    assert_exact_payload_inventory(output)
