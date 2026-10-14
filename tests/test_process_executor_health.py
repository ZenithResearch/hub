from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from services.process_composition import health
from services.process_composition.executor_health import (
    FrankHealthInvocation, MAX_OUTPUT, _bounded_process, frank_health,
)

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'PRIVATE_TOKEN_PROOF_PATH_SENTINEL'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def invocation(tmp_path):
    names = ('HUB_HEALTH_INSTALLED_PYTHON', 'HUB_HEALTH_FRANK_WHEEL', 'HUB_HEALTH_RUNTIME_WHEEL')
    if not all(os.environ.get(name) for name in names):
        if os.environ.get('HUB_HEALTH_REQUIRE_ARTIFACTS') == '1':
            pytest.fail('Supply all reviewed installed health artifacts for qualification')
        pytest.skip('Optional installed health drill: supply all HUB_HEALTH artifact paths')
    python, frank, runtime = (os.environ[name] for name in names)
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    config = private / 'frank.json'
    config.write_text(json.dumps({
        'schema': 'frank.config.v1', 'environment': 'test',
        'runtime_endpoint': 'http://127.0.0.1:1', 'journal': str(private / 'unused.db'),
        'allow_local_dev_untrusted': True,
        'integrations': {'schema': 'frank.integrations.v1', 'integrations': [], 'actors': []},
    }))
    config.chmod(0o600)
    return FrankHealthInvocation(python, digest(python), str(config), frank, digest(frank), runtime, digest(runtime))


def probe(invocation, **extra):
    return frank_health(invocation, frank_version='0.1.0', runtime_version='0.1.0',
                        environment='test', timeout=5, **extra)


@pytest.fixture
def http_endpoint():
    payloads = {
        '/live': (200, {'live': True}),
        '/ready': (200, {'ready': True, 'checks': {'storage': 'available',
                    'authority': 'local_dev_untrusted', 'credential': SECRET, 'raw_error': SECRET}}),
    }
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code, body = payloads[self.path]
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), payloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def composition(endpoint):
    value = json.loads((ROOT / 'config/process-execution.components.json').read_text())
    for component in value['components'].values():
        component['endpoint'] = endpoint
    return value


def public_body(**changes):
    return {'live': True, 'configuration': 'valid', 'adapter_ready': True, 'ready': True,
            'authority': 'local_dev_untrusted', 'integrations': [], **changes}


def test_actual_installed_cli_effect_disabled_health(invocation, http_endpoint):
    endpoint, _ = http_endpoint
    result = health(composition(endpoint), timeout=5, frank_invocation=invocation)
    assert all(item['ready'] for item in result.values())
    assert result['frank']['cli_ready'] and result['frank']['integration_count'] == 0
    assert result['frank']['native_authority_qualified'] is False
    text = json.dumps(result)
    assert SECRET not in text and invocation.config not in text and invocation.python not in text
    assert not Path(invocation.config).with_name('unused.db').exists()


def test_actual_cli_runtime_unavailable_has_runtime_owner_recovery(invocation, http_endpoint):
    endpoint, _ = http_endpoint
    config = composition(endpoint)
    config['components']['process-runtime']['endpoint'] = 'http://127.0.0.1:1'
    result = health(config, timeout=5, frank_invocation=invocation)
    assert result['frank']['live'] is True and result['frank']['cli_ready'] is True
    assert result['frank']['ready'] is False
    assert result['frank']['diagnostics'][0]['owner'] == 'process-runtime'
    assert result['frank']['reason'] == 'runtime_dependency_unavailable'
    assert 'Restore' in result['frank']['diagnostics'][0]['recovery']


def test_actual_production_cli_missing_native_authority(invocation):
    config = json.loads(Path(invocation.config).read_text())
    config.update(environment='production', allow_local_dev_untrusted=False)
    Path(invocation.config).write_text(json.dumps(config))
    result = probe(invocation)
    assert result['live'] is True and result['ready'] is False
    assert result['reason'] == 'native_authority_unqualified'
    assert result['diagnostics'][0]['owner'] == 'frank'


def test_actual_cli_invalid_config_errors_are_redacted(invocation):
    Path(invocation.config).write_text(SECRET)
    result = probe(invocation)
    assert result['reason'] == 'executor_health_command_failed'
    assert SECRET not in json.dumps(result) and invocation.config not in json.dumps(result)


def test_unconfigured_health_preserves_safe_unavailable(http_endpoint):
    endpoint, _ = http_endpoint
    result = health(composition(endpoint))['frank']
    assert result['live'] is None and result['ready'] is False
    assert result['reason'] == 'executor_health_command_required'


@pytest.mark.parametrize('change', ['public', 'symlink', 'missing', 'parent-public'])
def test_private_config_custody_denies_before_child(invocation, change):
    path = Path(invocation.config)
    if change == 'public': path.chmod(0o644)
    elif change == 'missing': path.unlink()
    elif change == 'parent-public': path.parent.chmod(0o755)
    else:
        target = path.with_name('target.json'); path.rename(target); path.symlink_to(target)
    with patch('services.process_composition.executor_health._bounded_process') as child:
        assert probe(invocation)['reason'] == 'operator_configuration_unavailable'
    child.assert_not_called()


@pytest.mark.parametrize('field', ['python_sha256', 'frank_sha256', 'runtime_sha256'])
def test_reviewed_artifact_pin_mismatch_denies_before_child(invocation, field):
    with patch('services.process_composition.executor_health._bounded_process') as child:
        result = probe(replace(invocation, **{field: '0' * 64}))
    assert result['reason'] == 'operator_artifact_unavailable'
    child.assert_not_called()


@pytest.mark.parametrize('raw', [b'not-json', b'[]', b'{"live":true,"live":false}',
    b'x' * (MAX_OUTPUT + 1), b'{"ready":NaN}',
    json.dumps(public_body(live='true')).encode(),
    json.dumps(public_body(authority='adapter_required')).encode(),
    json.dumps(public_body(raw_proof=SECRET)).encode()])
def test_malformed_oversized_or_inconsistent_cli_output_fail_closed(invocation, raw):
    with patch('services.process_composition.executor_health._bounded_process', return_value=(0, raw, b'')):
        result = probe(invocation)
    assert result['ready'] is False and result['reason'] == 'executor_health_output_invalid'
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('failure', ['error', 'stderr', 'timeout', 'oversized'])
def test_cli_failures_have_fixed_redacted_recovery(invocation, failure):
    effect = (subprocess.TimeoutExpired([SECRET], 2) if failure == 'timeout'
              else ValueError(SECRET) if failure == 'oversized' else None)
    response = (2 if failure == 'error' else 0, SECRET.encode(), SECRET.encode())
    with patch('services.process_composition.executor_health._bounded_process',
               side_effect=effect, return_value=response):
        result = probe(invocation)
    assert result['ready'] is False and result['diagnostics'][0]['owner'] == 'frank'
    assert SECRET not in json.dumps(result) and 'recovery' in result['diagnostics'][0]


def test_adapter_details_and_transport_identity_are_not_echoed(invocation):
    raw = json.dumps(public_body(integrations=[{'ready': False, 'integration_id': SECRET,
                                               'error': SECRET, 'token': SECRET}])).encode()
    with patch('services.process_composition.executor_health._bounded_process', return_value=(0, raw, b'')):
        result = probe(invocation)
    assert result['ready'] is False and result['reason'] == 'integration_unavailable'
    assert SECRET not in json.dumps(result) and result['integration_count'] == 1


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_subprocess_output_is_bounded_while_reading(stream):
    code = 'import sys; sys.' + stream + '.write("x" * 1000000)'
    with pytest.raises(ValueError):
        _bounded_process([sys.executable, '-I', '-c', code], 3)


def test_subprocess_timeout_is_bounded():
    with pytest.raises(subprocess.TimeoutExpired):
        _bounded_process([sys.executable, '-I', '-c', 'import time; time.sleep(5)'], 0.1)


@pytest.mark.parametrize('body', [b'[]', b'x' * (MAX_OUTPUT + 1),
    {'ready': 'true', 'checks': {}}, {'ready': False, 'checks': SECRET}])
def test_remote_health_contract_is_bounded_and_redacted(http_endpoint, body):
    endpoint, payloads = http_endpoint
    payloads['/ready'] = (503, body)
    result = health(composition(endpoint))
    assert result['process-runtime']['ready'] is False
    assert result['process-runtime']['reason'] == 'health_response_invalid'
    assert SECRET not in json.dumps(result)


def test_remote_storage_and_native_authority_have_owner_actions(http_endpoint):
    endpoint, payloads = http_endpoint
    payloads['/ready'] = (503, {'ready': False, 'checks': {'storage': 'unavailable', 'error': SECRET}})
    result = health(composition(endpoint))
    assert result['service-management']['diagnostics'][0]['reason'] == 'storage_unavailable'
    payloads['/ready'] = (503, {'ready': False, 'checks': {'storage': 'available', 'authority': 'default_deny'}})
    result = health(composition(endpoint))
    assert result['process-runtime']['diagnostics'][0]['reason'] == 'native_authority_unqualified'


def test_liveness_failure_never_becomes_ready(http_endpoint):
    endpoint, payloads = http_endpoint
    payloads['/live'] = (200, {'live': False})
    result = health(composition(endpoint))
    assert result['process-runtime']['ready'] is False


@pytest.mark.parametrize('checks', [{}, {'storage': 'available', 'authority': SECRET},
    {'storage': 'available', 'authority': 'configured_native_unqualified'}])
def test_remote_ready_label_cannot_hide_unqualified_checks(http_endpoint, checks):
    endpoint, payloads = http_endpoint
    payloads['/ready'] = (200, {'ready': True, 'checks': checks})
    result = health(composition(endpoint))
    assert result['process-runtime']['ready'] is False
    assert SECRET not in json.dumps(result)


def test_production_composition_rejects_local_dev_cli_readiness(invocation):
    result = frank_health(invocation, frank_version='0.1.0', runtime_version='0.1.0',
                          environment='production', timeout=5)
    assert result['cli_ready'] is True and result['ready'] is False
    assert result['reason'] == 'native_authority_unqualified'


def test_actual_child_ignores_inherited_source_overrides(invocation, tmp_path, monkeypatch):
    fake = tmp_path / 'fake-source'; fake.mkdir()
    package = fake / 'frank_executor'; package.mkdir()
    (package / '__init__.py').write_text('raise RuntimeError("' + SECRET + '")')
    monkeypatch.setenv('PYTHONPATH', str(fake))
    assert probe(invocation)['ready'] is True


def test_actual_hub_cli_operator_health_branch_and_partial_options(invocation, http_endpoint, tmp_path):
    endpoint, _ = http_endpoint
    directory = tmp_path / 'composition'; directory.mkdir()
    contracts = directory / 'process-contracts'; contracts.mkdir()
    for path in (ROOT / 'config/process-contracts').glob('*.json'):
        (contracts / path.name).write_bytes(path.read_bytes())
    config = directory / 'composition.json'
    config.write_text(json.dumps(composition(endpoint)))
    command = [sys.executable, '-B', '-m', 'services.process_composition', 'health', '--config', str(config)]
    options = ['--frank-python', invocation.python, '--frank-python-sha256', invocation.python_sha256,
               '--frank-config', invocation.config, '--frank-wheel', invocation.frank_wheel,
               '--frank-wheel-sha256', invocation.frank_sha256, '--runtime-wheel', invocation.runtime_wheel,
               '--runtime-wheel-sha256', invocation.runtime_sha256, '--health-timeout', '5']
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    result = subprocess.run(command + options, env=env, capture_output=True, timeout=20)
    assert result.returncode == 0 and json.loads(result.stdout)['frank']['ready'] is True
    assert invocation.config.encode() not in result.stdout and SECRET.encode() not in result.stdout
    rejected = subprocess.run(command + ['--frank-config', SECRET], env=env, capture_output=True, timeout=10)
    assert rejected.returncode == 2 and rejected.stderr == b'Composition health check failed\n'
