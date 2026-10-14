"""Installed-image verification; stdlib only, no source or authority imports."""
import argparse
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import platform
import sys


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(inputs):
    assert sys.platform == 'linux' and platform.machine() == 'aarch64'
    assert platform.python_version() == inputs['base']['python_version']
    prefix = Path(sys.prefix).resolve()
    expected = {item['name']: item for item in inputs['packages']}
    installed = {}
    for dist in metadata.distributions():
        name = dist.metadata['Name'].lower().replace('_', '-')
        assert name not in installed
        files = {}
        for member in dist.files or []:
            if str(member).endswith('.pyc'):
                continue
            path = Path(dist.locate_file(member)).resolve()
            assert path.is_relative_to(prefix) and path.is_file()
            files[str(path.relative_to(prefix))] = sha(path)
        installed[name] = {'version': dist.version, 'files': files}
    # pip is supplied by the digest-pinned Python base's ensurepip toolchain.
    assert set(installed) - set(expected) <= {'pip', 'setuptools'}
    for name, package in expected.items():
        dist = metadata.distribution(name)
        assert dist.version == package['version']
        for member, expected_sha in package['members'].items():
            if '.data/data/' in member:
                path = prefix / member.split('.data/data/', 1)[1]
            else:
                assert '.data/' not in member
                path = Path(dist.locate_file(member)).resolve()
            assert path.is_relative_to(prefix) and sha(path) == expected_sha
        for module in package['modules']:
            spec = importlib.util.find_spec(module)
            target = Path(dist.locate_file(module + '/__init__.py')).resolve()
            assert spec and spec.origin and Path(spec.origin).resolve() == target
    return {'schema': 'hub.component-image-installed.v1', 'component': inputs['component'],
            'python_version': platform.python_version(), 'platform': 'linux/arm64',
            'input_sha256': hashlib.sha256(canonical(inputs)).hexdigest(),
            'packages': installed, 'base_packages_sha256': sha(Path('/opt/evidence/base-packages.txt')),
            'base_packages': Path('/opt/evidence/base-packages.txt').read_text().splitlines(),
            'native_helpers': [], 'production_ready': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-build', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = inventory(json.loads(args.verify_build.read_bytes()))
    retained = Path('/opt/evidence/installed.json')
    if retained.exists():
        assert canonical(result) == canonical(json.loads(retained.read_bytes()))
    raw = canonical(result) + b'\n'
    if args.output:
        args.output.write_bytes(raw)
    else:
        sys.stdout.buffer.write(raw)


if __name__ == '__main__':
    main()
