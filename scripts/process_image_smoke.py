"""No-network image smoke; use unchanged owner CLIs with disposable storage."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib import request, error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('component', choices=('process-runtime', 'service-management', 'frank'))
    args = parser.parse_args()
    inputs = json.loads(Path('/opt/evidence/build-inputs.json').read_bytes())
    assert inputs['component'] == args.component
    subprocess.run([sys.executable, '-I', '-B', '/opt/image-tools/inventory.py', '--verify-build',
                    '/opt/evidence/build-inputs.json', '--output', '/tmp/verified-inventory.json'], check=True, timeout=30)
    with tempfile.TemporaryDirectory(prefix='component-smoke-', dir='/data') as temporary:
        root = Path(temporary)
        if args.component == 'frank':
            config = root / 'fixture.json'
            config.write_text(json.dumps({'schema': 'frank.config.v1', 'environment': 'production',
                'runtime_endpoint': 'http://127.0.0.1:1', 'journal': str(root/'frank.db'),
                'allow_local_dev_untrusted': False,
                'integrations': {'schema': 'frank.integrations.v1', 'integrations': [], 'actors': []}}))
            config.chmod(0o600)
            command = [sys.executable, '-I', '-B', '-m', 'frank_executor']
            health = subprocess.run(command+['health', '--config', str(config)], capture_output=True, timeout=15)
            assert health.returncode == 0
            body = json.loads(health.stdout)
            assert body['live'] is True and body['ready'] is False and body['authority'] == 'adapter_required'
            rejected = subprocess.run(command+['register', '--config', str(config), '--actor', 'fixture'], capture_output=True, timeout=15)
            assert rejected.returncode == 2 and b'qualified native secS' in rejected.stderr
        else:
            module = 'process_runtime' if args.component == 'process-runtime' else 'service_management'
            child = subprocess.Popen([sys.executable, '-I', '-B', '-m', module, '--database', str(root/'fixture.db'),
                '--port', '18992', '--environment', 'production'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                opener = request.build_opener(request.ProxyHandler({}))
                deadline = time.monotonic()+15
                while True:
                    assert child.poll() is None
                    try:
                        with opener.open('http://127.0.0.1:18992/live', timeout=1) as response:
                            assert json.load(response)['live'] is True
                        break
                    except OSError:
                        assert time.monotonic() < deadline
                        time.sleep(.1)
                try:
                    opener.open('http://127.0.0.1:18992/ready', timeout=2)
                except error.HTTPError as response:
                    assert response.code == 503
                    body = json.load(response)
                    assert body['ready'] is False and body['checks']['authority'] == 'default_deny'
                else:
                    raise AssertionError('Production unexpectedly ready')
            finally:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=5)
    print(json.dumps({'schema': 'hub.component-image-smoke.v1', 'component': args.component,
        'installed_inventory_verified': True, 'production_default_deny': True,
        'external_sends': 0, 'native_helpers': [], 'production_qualified': False}, sort_keys=True))


if __name__ == '__main__':
    main()
