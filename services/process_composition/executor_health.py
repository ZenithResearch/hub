"""Operator-only, bounded public health probes; no credentials or business state."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time

MAX_OUTPUT = 16_384


@dataclass(frozen=True)
class FrankHealthInvocation:
    python: str
    python_sha256: str
    config: str
    frank_wheel: str
    frank_sha256: str
    runtime_wheel: str
    runtime_sha256: str


def diagnostic(owner, reason):
    actions = {
        'executor_health_command_required': 'Supply the private operator Frank health invocation.',
        'operator_configuration_unavailable': 'Check private Frank configuration custody and access.',
        'operator_artifact_unavailable': 'Restore the reviewed Python and installed component artifacts.',
        'executor_health_timeout': 'Inspect Frank adapter startup and retry the bounded health probe.',
        'executor_health_output_invalid': 'Inspect Frank public health contract compatibility.',
        'executor_health_command_failed': 'Inspect Frank configuration and adapter startup locally.',
        'native_authority_unqualified': 'Qualify the owner native authority and result verification path.',
        'integration_unavailable': 'Inspect the owner configured adapter lifecycle and provider qualification.',
        'runtime_dependency_unavailable': 'Restore process-runtime readiness before dispatching work.',
        'storage_unavailable': 'Restore the owner private storage and verify readiness.',
        'endpoint_unavailable': 'Restore the owner endpoint and inspect liveness locally.',
        'readiness_unavailable': 'Inspect the owner readiness and dependency checks locally.',
        'health_response_invalid': 'Inspect the owner public health response contract.',
    }
    return {'owner': owner, 'reason': reason, 'recovery': actions[reason]}


def unavailable(reason):
    return {'owner': 'frank', 'live': None, 'ready': False,
            'reason': reason, 'diagnostics': [diagnostic('frank', reason)]}


def decode_health(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_OUTPUT:
        raise ValueError('health response rejected')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('health response rejected')
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(value) is not dict:
        raise ValueError('health response rejected')
    return value


def bounded_timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 10:
        raise ValueError('health timeout rejected')
    return value


def _file_digest(path, expected):
    if not path.is_absolute() or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('artifact rejected')
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('artifact rejected')
    with path.open('rb') as source:
        actual = hashlib.file_digest(source, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError('artifact rejected')


# The child verifies installed wheel bytes and isolated import origins before
# importing the public CLI. This is an artifact check, never authority admission.
_CHILD = """
import hashlib, importlib.metadata as metadata, importlib.util, json
from pathlib import Path
import runpy, sys, zipfile
prefix = Path(sys.prefix).resolve()
for package, version, raw_path, expected in json.loads(sys.argv[1]):
    wheel_path = Path(raw_path)
    assert hashlib.sha256(wheel_path.read_bytes()).hexdigest() == expected
    dist = metadata.distribution(package)
    assert dist.version == version
    with zipfile.ZipFile(wheel_path) as wheel:
        for member in wheel.namelist():
            if member.endswith('/') or member.endswith('/RECORD'): continue
            assert not member.startswith('/') and '..' not in Path(member).parts
            if '.data/data/' in member:
                target = prefix / member.split('.data/data/', 1)[1]
            else:
                assert '.data/' not in member
                target = Path(dist.locate_file(member)).resolve()
            assert target.is_relative_to(prefix) and target.read_bytes() == wheel.read(member)
            parts = Path(member).parts
            if len(parts) == 2 and parts[1] == '__init__.py':
                spec = importlib.util.find_spec(parts[0])
                assert spec and spec.origin and Path(spec.origin).resolve() == target
sys.argv = ['frank_executor', 'health', '--config', sys.argv[2]]
runpy.run_module('frank_executor', run_name='__main__')
"""


def _bounded_process(command, timeout):
    """Bound both pipes while reading; do not accumulate arbitrary child output."""
    with tempfile.TemporaryDirectory(prefix='hub-executor-health-') as directory:
        child = subprocess.Popen(command, cwd=directory, env={'PATH': os.defpath},
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        data = {'stdout': bytearray(), 'stderr': bytearray()}
        try:
            with selectors.DefaultSelector() as selected:
                for name in data:
                    selected.register(getattr(child, name), selectors.EVENT_READ, name)
                deadline = time.monotonic() + timeout
                while selected.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in selected.select(remaining):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selected.unregister(key.fileobj)
                            continue
                        data[key.data].extend(chunk)
                        if len(data[key.data]) > MAX_OUTPUT:
                            raise ValueError('health output rejected')
                code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            return code, bytes(data['stdout']), bytes(data['stderr'])
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)
            child.stdout.close()
            child.stderr.close()


def frank_health(invocation, *, frank_version, runtime_version, environment, timeout=2):
    if invocation is None:
        return unavailable('executor_health_command_required')
    if not isinstance(invocation, FrankHealthInvocation):
        return unavailable('operator_configuration_unavailable')
    try:
        timeout = bounded_timeout(timeout)
        path = Path(invocation.config)
        info = path.lstat()
        parent = path.parent.stat()
        if (not path.is_absolute() or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o077
                or parent.st_uid != os.getuid() or parent.st_mode & 0o077):
            raise ValueError('private configuration rejected')
    except (OSError, ValueError, TypeError):
        return unavailable('operator_configuration_unavailable')
    try:
        _file_digest(Path(invocation.python), invocation.python_sha256)
        wheels = [('frank-executor', frank_version, invocation.frank_wheel, invocation.frank_sha256),
                  ('process-runtime', runtime_version, invocation.runtime_wheel, invocation.runtime_sha256)]
        for _, _, wheel, digest in wheels:
            path = Path(wheel)
            if path.is_symlink():
                raise ValueError('artifact rejected')
            _file_digest(path, digest)
    except (OSError, ValueError, TypeError):
        return unavailable('operator_artifact_unavailable')
    try:
        code, stdout, stderr = _bounded_process(
            [invocation.python, '-I', '-B', '-c', _CHILD, json.dumps(wheels), invocation.config], timeout)
    except subprocess.TimeoutExpired:
        return unavailable('executor_health_timeout')
    except ValueError:
        return unavailable('executor_health_output_invalid')
    except (OSError, subprocess.SubprocessError):
        return unavailable('executor_health_command_failed')
    if code != 0 or stderr:
        return unavailable('executor_health_command_failed')
    try:
        body = decode_health(stdout)
        if (set(body) != {'live', 'configuration', 'adapter_ready', 'ready', 'authority', 'integrations'}
                or any(type(body[k]) is not bool for k in ('live', 'adapter_ready', 'ready'))
                or body['configuration'] != 'valid'
                or body['authority'] not in ('local_dev_untrusted', 'adapter_required')
                or type(body['integrations']) is not list or len(body['integrations']) > 32
                or any(type(item) is not dict or type(item.get('ready')) is not bool
                       for item in body['integrations'])
                or (body['ready'] and (not body['live'] or not body['adapter_ready']
                                      or body['authority'] != 'local_dev_untrusted'))):
            raise ValueError('health response rejected')
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return unavailable('executor_health_output_invalid')
    adapter_ready = body['adapter_ready'] and all(item['ready'] for item in body['integrations'])
    ready = body['ready'] and adapter_ready and environment != 'production'
    result = {'owner': 'frank', 'live': body['live'], 'ready': ready, 'cli_ready': body['ready'],
              'configuration': 'valid', 'adapter_ready': adapter_ready,
              'authority': body['authority'], 'integration_count': len(body['integrations']),
              'native_authority_qualified': False, 'diagnostics': []}
    if not ready:
        reason = ('native_authority_unqualified' if body['authority'] == 'adapter_required'
                  or environment == 'production' else 'integration_unavailable' if not adapter_ready
                  else 'readiness_unavailable')
        result.update(reason=reason, diagnostics=[diagnostic('frank', reason)])
    return result


def http_readiness(owner, result, body):
    """Copy only closed readiness statuses, never arbitrary remote check details."""
    checks = body.get('checks')
    if type(checks) is not dict:
        raise ValueError('health response rejected')
    allowed = {
        'storage': {'available', 'unavailable', 'unknown'},
        'authority': {'local_dev_untrusted', 'default_deny', 'unqualified', 'configured_native_unqualified'},
        'verification': {'available', 'unavailable'},
        'qualification': {'unqualified'},
    }
    result['checks'] = {key: checks[key] if type(checks.get(key)) is str and checks[key] in values
                        else 'unknown' for key, values in allowed.items() if key in checks}
    if result['checks'].get('storage') != 'available' or result['checks'].get('authority') != 'local_dev_untrusted':
        result['ready'] = False
    if not result['ready']:
        reason = ('storage_unavailable' if result['checks'].get('storage') == 'unavailable'
                  else 'native_authority_unqualified' if result['checks'].get('authority') in
                  {'default_deny', 'unqualified', 'configured_native_unqualified'}
                  else 'readiness_unavailable')
        result.update(reason=reason, diagnostics=[diagnostic(owner, reason)])


def require_runtime_dependency(results):
    frank = results['frank']
    if frank.get('ready') is True and results['process-runtime']['ready'] is not True:
        frank.update(ready=False, reason='runtime_dependency_unavailable',
                     diagnostics=[diagnostic('process-runtime', 'runtime_dependency_unavailable')])
    return results
