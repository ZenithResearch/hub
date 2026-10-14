"""Prepare, verify and locally build immutable component images; never push."""
from __future__ import annotations

import argparse
import base64
import csv
import email
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import zlib

# Validate the isolated host closure before importing its dependency parser.
_host_spec = importlib.util.spec_from_file_location('image_host_tools', Path(__file__).with_name('bootstrap_process_image_tools.py'))
_host_tools = importlib.util.module_from_spec(_host_spec)
_host_spec.loader.exec_module(_host_tools)
HOST_TOOLS = _host_tools.verify_environment()
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

HUB = Path(__file__).resolve().parents[1]
OWNERS = {'process-runtime': 'process-runtime', 'service-management': 'service-management', 'frank': 'frank-executor'}
MODULES = {'process-runtime': 'process_runtime', 'service-management': 'service_management', 'frank': 'frank_executor'}
ENVIRONMENT = {'python_version': '3.12', 'python_full_version': '3.12.12', 'extra': '',
               'sys_platform': 'linux', 'os_name': 'posix', 'platform_machine': 'aarch64',
               'platform_python_implementation': 'CPython', 'implementation_name': 'cpython',
               'implementation_version': '3.12.12', 'platform_release': '', 'platform_system': 'Linux', 'platform_version': ''}
MAX_OCI_BYTES = 2*1024**3
MAX_OCI_MEMBERS = 4096
MAX_JSON_BYTES = 4*1024**2
MAX_LAYERS = 128
MAX_COMPRESSED_LAYER = 512*1024**2
MAX_LAYER_BYTES = 1024**3
MAX_TOTAL_LAYER_BYTES = 2*1024**3
MAX_LAYER_MEMBERS = 100000
MAX_TAR_HEADERS = 200000


class ImageInputError(ValueError):
    pass


class BoundedTarInfo(tarfile.TarInfo):
    # tarfile consumes extended headers before yielding the logical member.
    # Bound those allocations as well as the returned member metadata.
    def _proc_pax(self, archive):
        require(self.size <= 65536, 'Oversized tar extended metadata')
        return self._extended(super()._proc_pax, archive)

    def _proc_gnulong(self, archive):
        require(self.size <= 65536, 'Oversized tar extended name')
        return self._extended(super()._proc_gnulong, archive)

    def _proc_member(self, archive):
        archive._image_header_count = getattr(archive, '_image_header_count', 0)+1
        require(archive._image_header_count <= MAX_TAR_HEADERS, 'Excessive physical tar headers')
        return super()._proc_member(archive)

    def _extended(self, operation, archive):
        depth = getattr(archive, '_image_metadata_depth', 0)
        require(depth < 16, 'Excessive nested tar metadata')
        archive._image_metadata_depth = depth+1
        try: return operation(archive)
        finally: archive._image_metadata_depth = depth


def require(value, detail):
    if not value:
        raise ImageInputError(detail)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def read_json(path):
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value, 'Duplicate JSON field')
            value[key] = item
        return value
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs)


def entrypoint(owner):
    return ['/opt/component/bin/python', '-I', '-B', '-m', MODULES[owner]]


def wheel_inventory(path, expected, platform):
    require(path.is_file() and not path.is_symlink() and path.name == expected['filename'], 'Missing or unsafe wheel')
    require(sha(path) == expected['sha256'], 'Wheel digest mismatch')
    name, version, _, tags = parse_wheel_filename(path.name)
    require(platform == 'linux/arm64', 'Unreviewed image platform')
    platforms = ['manylinux_2_'+str(n)+'_aarch64' for n in range(36, 16, -1)] + ['manylinux2014_aarch64', 'linux_aarch64']
    supported = set(cpython_tags((3, 12), abis=['cp312', 'abi3', 'none'], platforms=platforms))
    supported.update(compatible_tags((3, 12), interpreter='cp312', platforms=platforms))
    require(bool(tags & supported), 'Wheel is incompatible with Linux arm64 CPython3.12')
    require(str(name) == canonicalize_name(expected['name']) and str(version) == expected['version'], 'Wheel filename identity mismatch')
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        require(len(names) == len(set(names)) and len(names) <= 10000, 'Duplicate or excessive wheel members')
        require(sum(item.file_size for item in wheel.infolist()) <= 256*1024*1024, 'Oversized wheel')
        for item in wheel.infolist():
            require(not item.filename.startswith('/') and '\\' not in item.filename and '..' not in Path(item.filename).parts,
                    'Unsafe wheel member')
            require((item.external_attr >> 16) & 0o170000 != 0o120000, 'Wheel symlink rejected')
            require(not wheel.read(item)[:4] in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe',
                    b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'),
                    'Darwin executable rejected')
        metadata = [n for n in names if n.endswith('.dist-info/METADATA')]
        records = [n for n in names if n.endswith('.dist-info/RECORD')]
        require(len(metadata) == len(records) == 1, 'Wheel metadata identity missing')
        meta = email.message_from_bytes(wheel.read(metadata[0]))
        require(canonicalize_name(meta['Name']) == name and meta['Version'] == str(version), 'Wheel metadata identity mismatch')
        require(SpecifierSet(meta.get('Requires-Python', '')).contains('3.12.12'), 'Wheel Python requirement mismatch')
        members = {n: hashlib.sha256(wheel.read(n)).hexdigest() for n in names if not n.endswith('/') and n != records[0]}
        seen = set()
        for member, digest, length in csv.reader(io.StringIO(wheel.read(records[0]).decode())):
            require(member not in seen and member in names, 'Wheel RECORD mismatch'); seen.add(member)
            if member == records[0]:
                require(not digest and not length, 'Wheel RECORD self hash rejected'); continue
            actual = wheel.read(member)
            expected_digest = 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(actual).digest()).decode().rstrip('=')
            require(digest == expected_digest and int(length) == len(actual), 'Wheel RECORD integrity mismatch')
        require(seen == set(names), 'Unrecorded wheel member')
    return {'name': str(name), 'version': str(version), 'filename': path.name, 'sha256': expected['sha256'],
            'requires': meta.get_all('Requires-Dist', []), 'members': members,
            'modules': sorted({Path(n).parts[0] for n in members if len(Path(n).parts) == 2 and n.endswith('/__init__.py')})}


def validate_policy(policy):
    require(set(policy) == {'schema', 'base', 'source_date_epoch', 'tuple_manifest_sha256', 'components', 'wheels', 'contracts', 'native_helpers', 'production_ready'}, 'Unsupported image policy')
    require(policy['schema'] == 'hub.component-image-policy.v1' and policy['production_ready'] is False
            and policy['native_helpers'] == [] and type(policy['source_date_epoch']) is int and policy['source_date_epoch'] == 0,
            'Image qualification/native boundary mismatch')
    base = policy['base']
    require(set(base) == {'platform', 'python_version', 'reference', 'manifest_digest', 'index_digest', 'config_digest', 'rootfs_diff_ids'}
            and base['platform'] == 'linux/arm64' and base['python_version'] == '3.12.12'
            and re.fullmatch('sha256:[a-f0-9]{64}', base['manifest_digest'])
            and base['reference'] == 'docker.io/library/python@'+base['manifest_digest']
            and re.fullmatch('sha256:[a-f0-9]{64}', base['index_digest'])
            and re.fullmatch('sha256:[a-f0-9]{64}', base['config_digest']), 'Exact reviewed Python base required')
    require(type(base['rootfs_diff_ids']) is list and base['rootfs_diff_ids']
            and all(re.fullmatch('sha256:[a-f0-9]{64}', item) for item in base['rootfs_diff_ids']), 'Base layer identities required')
    require(set(policy['components']) == set(OWNERS), 'Independent component set mismatch')
    for owner, item in policy['components'].items():
        require(set(item) == {'package', 'version', 'source_commit', 'wheel_sha256'} and item['package'] == OWNERS[owner]
                and item['version'] == '0.1.0' and re.fullmatch('[a-f0-9]{40}', item['source_commit'])
                and re.fullmatch('[a-f0-9]{64}', item['wheel_sha256']), 'Component source/artifact pin required')
    for wheel in policy['wheels']:
        require(set(wheel) == {'name', 'version', 'filename', 'sha256'} and Path(wheel['filename']).name == wheel['filename']
                and re.fullmatch('[a-f0-9]{64}', wheel['sha256']), 'Closed wheel identity required')
    for contract in policy['contracts'].values():
        require(set(contract) == {'package', 'member', 'sha256'} and re.fullmatch('[a-f0-9]{64}', contract['sha256']), 'Closed contract pin required')


def closure(roots, packages):
    found = set(); pending = list(roots)
    while pending:
        name = canonicalize_name(pending.pop())
        require(name in packages, 'Missing transitive wheel')
        if name in found: continue
        found.add(name)
        for raw in packages[name]['requires']:
            dependency = Requirement(raw)
            if dependency.marker and not dependency.marker.evaluate(ENVIRONMENT): continue
            target = canonicalize_name(dependency.name)
            require(not dependency.url and not dependency.extras and target in packages, 'Unadmitted dependency URL/extra or missing wheel')
            require(dependency.specifier.contains(packages[target]['version']), 'Incompatible transitive dependency')
            pending.append(target)
    return sorted(found)


def prepare_contexts(policy, tuple_path, wheelhouse, destination):
    validate_policy(policy)
    require(not destination.exists(), 'Destination already exists')
    require(sha(tuple_path) == policy['tuple_manifest_sha256'], 'Installed tuple identity mismatch')
    artifact = read_json(tuple_path)
    require(artifact['schema'] == 'hub.process-artifact-tuple.v1' and artifact['production_ready'] is False, 'Unsupported tuple')
    for owner, item in policy['components'].items():
        pin = artifact['components'][owner]
        require(all(pin[k] == item[k] for k in ('package', 'version', 'wheel_sha256')), 'Component tuple mismatch')
    require(set(p.name for p in wheelhouse.iterdir()) == {w['filename'] for w in policy['wheels']}, 'Missing or extra wheelhouse input')
    packages = {}
    for item in policy['wheels']:
        record = wheel_inventory(wheelhouse/item['filename'], item, policy['base']['platform'])
        require(record['name'] not in packages, 'Duplicate package identity')
        packages[record['name']] = record
    for owner, component in policy['components'].items():
        admitted = packages.get(canonicalize_name(component['package']))
        pin = artifact['components'][owner]
        require(admitted is not None and all(
            admitted[actual] == component[claimed] == pin[claimed]
            for actual, claimed in [('name', 'package'), ('version', 'version'), ('sha256', 'wheel_sha256')]),
            'Admitted component differs from source/tuple artifact pin')
    versions = {canonicalize_name(k): v['version'] for k, v in artifact['dependencies'].items()}
    require({k: v['version'] for k, v in packages.items()} == versions, 'Dependency tuple version mismatch')
    for contract in policy['contracts'].values():
        require(packages[contract['package']]['members'].get(contract['member']) == contract['sha256'], 'Contract hash mismatch')
    if 'runtime-sdk' in policy['contracts'] and 'frank-runtime' in policy['contracts']:
        require(policy['contracts']['runtime-sdk']['sha256'] == policy['contracts']['frank-runtime']['sha256'], 'Incompatible runtime SDK contract')
    inputs = {}
    template = (HUB/'docker/process-components/Dockerfile').read_text()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='component-context-stage-', dir=destination.parent) as temporary:
        stage = Path(temporary)/'prepared'; stage.mkdir()
        for owner, component in policy['components'].items():
            roots = [component['package']] + (['process-runtime'] if owner == 'service-management' else [])
            selected = closure(roots, packages)
            target = stage/owner; target.mkdir(); (target/'wheelhouse').mkdir()
            admitted = [packages[k] for k in selected]
            build = {'schema': 'hub.component-image-input.v1', 'component': owner, 'source': component,
                     'base': policy['base'], 'policy_sha256': hashlib.sha256(canonical(policy)).hexdigest(),
                     'tuple_manifest_sha256': policy['tuple_manifest_sha256'], 'packages': admitted,
                     'host_tools': HOST_TOOLS, 'native_helpers': [], 'production_ready': False}
            (target/'build-inputs.json').write_bytes(canonical(build)+b'\n')
            (target/'requirements.lock').write_text(''.join(p['name']+'=='+p['version']+' --hash=sha256:'+p['sha256']+'\n' for p in admitted))
            for package in admitted: shutil.copyfile(wheelhouse/package['filename'], target/'wheelhouse'/package['filename'])
            dockerfile = template.replace('__BASE__', policy['base']['reference']).replace('__COMPONENT__', owner).replace('__COMMIT__', component['source_commit']).replace('__ENTRYPOINT__', json.dumps(entrypoint(owner)))
            (target/'Dockerfile').write_text(dockerfile)
            shutil.copyfile(HUB/'scripts/process_image_inventory.py', target/'inventory.py')
            shutil.copyfile(HUB/'scripts/process_image_smoke.py', target/'smoke.py')
            inputs[owner] = {str(p.relative_to(target)): sha(p) for p in sorted(target.rglob('*')) if p.is_file()}
        manifest = {'schema': 'hub.component-image-contexts.v1', 'policy': policy, 'components': inputs, 'production_ready': False}
        (stage/'contexts.json').write_bytes(canonical(manifest)+b'\n')
        os.replace(stage, destination)
    return manifest


def validate_prepared(root, expected_sha):
    require(root.is_dir() and not root.is_symlink() and sha(root/'contexts.json') == expected_sha, 'Context review digest mismatch')
    manifest = read_json(root/'contexts.json'); validate_policy(manifest['policy'])
    require(set(manifest) == {'schema', 'policy', 'components', 'production_ready'}
            and manifest['schema'] == 'hub.component-image-contexts.v1' and manifest['production_ready'] is False, 'Unsupported context')
    require(set(manifest['components']) == set(OWNERS), 'Context component mismatch')
    expected = {'contexts.json'}
    for owner, files in manifest['components'].items():
        for name, digest in files.items():
            require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'Unsafe context path')
            path = root/owner/name
            require(path.is_file() and not path.is_symlink() and sha(path) == digest, 'Context file drift')
            expected.add(owner+'/'+name)
    actual = set()
    for p in root.rglob('*'):
        require(not p.is_symlink(), 'Context symlink rejected')
        require(p.is_file() or p.is_dir(), 'Special context file rejected')
        if p.is_file(): actual.add(str(p.relative_to(root)))
    require(actual == expected, 'Unadmitted context data')
    return manifest


def safe_tar_path(value, *, link_parent=None):
    require(type(value) is str and value and '\\' not in value and '\x00' not in value, 'Unsafe tar path')
    parts = [] if link_parent is None or value.startswith('/') else list(link_parent)
    require(link_parent is not None or not value.startswith('/'), 'Absolute tar member path')
    for part in PurePosixPath(value).parts:
        if part in ('/', '.', ''): continue
        if part == '..':
            require(parts, 'Tar path escapes container root'); parts.pop()
        else: parts.append(part)
    return '/'.join(parts)


def layer_diff_id(source, media, total):
    require(media in ('application/vnd.oci.image.layer.v1.tar+gzip', 'application/vnd.oci.image.layer.v1.tar'),
            'Unsupported layer compression/format')
    digest = hashlib.sha256(); size = 0
    stream = gzip.GzipFile(fileobj=source, mode='rb') if media.endswith('+gzip') else source
    try:
        with tempfile.SpooledTemporaryFile(max_size=1024**2) as raw:
            while chunk := stream.read(1024**2):
                size += len(chunk)
                require(size <= MAX_LAYER_BYTES and total+size <= MAX_TOTAL_LAYER_BYTES, 'Decompressed layer exceeds bound')
                digest.update(chunk); raw.write(chunk)
            require(size >= 1024 and size % 512 == 0, 'Invalid layer tar size')
            raw.seek(0)
            with tarfile.open(fileobj=raw, mode='r:', tarinfo=BoundedTarInfo) as layer:
                count = 0; names = set()
                for item in layer:
                    count += 1
                    require(count <= MAX_LAYER_MEMBERS, 'Excessive layer member count')
                    name = safe_tar_path(item.name)
                    require(name not in names, 'Duplicate layer member'); names.add(name)
                    require(item.size <= MAX_LAYER_BYTES and not item.sparse and
                            (item.isfile() or item.isdir() or item.issym() or item.islnk()), 'Unsupported layer member')
                    if item.issym() or item.islnk():
                        # No paths are extracted. Links are interpreted only in
                        # the container root; relative parent links are valid
                        # when they cannot escape it (as in upstream bases).
                        parent = PurePosixPath(name).parent.parts if item.issym() else ()
                        safe_tar_path(item.linkname, link_parent=parent)
            # A valid tar ends in zero blocks, never hidden appended payload.
            raw.seek(layer.offset)
            while chunk := raw.read(1024**2):
                require(not any(chunk), 'Nonzero trailing layer tar data')
        return 'sha256:'+digest.hexdigest(), size
    finally:
        if stream is not source: stream.close()


def _read_oci(path, *, base_export=False):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_OCI_BYTES
            and path.stat().st_size % 512 == 0, 'Oversized/unsafe OCI archive')
    try:
      with tarfile.open(path, mode='r:', tarinfo=BoundedTarInfo) as archive:
        members = {}
        for member in archive:
            require(len(members) < MAX_OCI_MEMBERS, 'Excessive OCI member count')
            name = safe_tar_path(member.name)
            require(name not in members and (member.isfile() or member.isdir()) and not member.sparse,
                    'Duplicate/unsafe OCI member')
            require(member.size <= MAX_COMPRESSED_LAYER, 'Oversized OCI member')
            members[name] = member
        archive.fileobj.seek(archive.offset)
        while chunk := archive.fileobj.read(1024**2):
            require(not any(chunk), 'Nonzero trailing OCI tar data')
        def read(name, bound=MAX_JSON_BYTES):
            require(name in members and members[name].isfile() and members[name].size <= bound, 'Missing/oversized OCI metadata')
            with archive.extractfile(members[name]) as stream: return stream.read(bound+1)
        require(json.loads(read('oci-layout')) == {'imageLayoutVersion': '1.0.0'}, 'Unsupported OCI layout')
        index = json.loads(read('index.json'))
        require(index['schemaVersion'] == 2 and len(index['manifests']) == 1, 'Unexpected image/attestation count')
        def blob(descriptor, bound):
            require(type(descriptor) is dict and re.fullmatch('sha256:[a-f0-9]{64}', descriptor['digest'])
                    and type(descriptor['size']) is int and 0 <= descriptor['size'] <= bound, 'Unsupported OCI descriptor')
            name = 'blobs/sha256/'+descriptor['digest'][7:]
            require(name in members and members[name].isfile() and members[name].size == descriptor['size'], 'OCI blob size mismatch')
            temporary = tempfile.SpooledTemporaryFile(max_size=1024**2)
            try:
                digest = hashlib.sha256(); size = 0
                with archive.extractfile(members[name]) as stream:
                    while chunk := stream.read(1024**2):
                        size += len(chunk); require(size <= bound, 'OCI blob exceeds bound')
                        digest.update(chunk); temporary.write(chunk)
                require(size == descriptor['size'] and 'sha256:'+digest.hexdigest() == descriptor['digest'], 'OCI blob digest mismatch')
                temporary.seek(0); return temporary
            except BaseException:
                temporary.close(); raise
        require(index['manifests'][0]['mediaType'] == 'application/vnd.oci.image.manifest.v1+json', 'Unsupported image manifest format')
        with blob(index['manifests'][0], MAX_JSON_BYTES) as raw: manifest = json.load(raw)
        require(manifest['schemaVersion'] == 2 and manifest['config']['mediaType'] == 'application/vnd.oci.image.config.v1+json',
                'Unsupported OCI manifest/config format')
        with blob(manifest['config'], MAX_JSON_BYTES) as raw: config = json.load(raw)
        layers = manifest['layers']; diff_ids = config['rootfs']['diff_ids']
        require(type(layers) is list and 0 < len(layers) <= MAX_LAYERS and config['rootfs']['type'] == 'layers'
                and type(diff_ids) is list and len(diff_ids) == len(layers)
                and all(type(d) is str and re.fullmatch('sha256:[a-f0-9]{64}', d) for d in diff_ids), 'Invalid layer/diff-ID count')
        computed = []; total = 0
        for position, descriptor in enumerate(layers):
            with blob(descriptor, MAX_COMPRESSED_LAYER) as raw:
                diff_id, size = layer_diff_id(raw, descriptor['mediaType'], total)
            require(diff_id == diff_ids[position], 'Actual layer filesystem differs from ordered diff-ID')
            computed.append(diff_id); total += size
        descriptors = [index['manifests'][0], manifest['config'], *layers]
        expected_files = {'oci-layout', 'index.json'} | {'blobs/sha256/'+d['digest'][7:] for d in descriptors}
        if base_export and 'manifest.json' in members:
            sidecar = json.loads(read('manifest.json'))
            require(type(sidecar) is list and len(sidecar) == 1 and type(sidecar[0]) is dict
                    and set(sidecar[0]) == {'Config', 'Layers', 'RepoTags'}
                    and sidecar[0]['RepoTags'] in (None, [])
                    and sidecar[0]['Config'] == 'blobs/sha256/'+manifest['config']['digest'][7:]
                    and sidecar[0]['Layers'] == ['blobs/sha256/'+layer['digest'][7:] for layer in layers],
                    'Docker save sidecar differs from validated OCI graph')
            expected_files.add('manifest.json')
        require({name for name, member in members.items() if member.isfile()} == expected_files,
                'Unreferenced/unadmitted OCI data')
        require(all(name in ('', 'blobs', 'blobs/sha256') for name, member in members.items() if member.isdir()),
                'Unadmitted OCI directory')
        require(config['architecture'] == 'arm64' and config['os'] == 'linux', 'Wrong image platform')
        receipt = {'manifest_digest': index['manifests'][0]['digest'], 'config_digest': manifest['config']['digest'],
                'archive_sha256': sha(path), 'layer_digests': [x['digest'] for x in layers],
                'rootfs_diff_ids': computed, 'uncompressed_layer_bytes': total}
        return {'receipt': receipt, 'config': config, 'manifest': manifest,
                'manifest_descriptor': index['manifests'][0]}
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, zlib.error, KeyError, TypeError, ValueError) as error:
        if isinstance(error, ImageInputError): raise
        raise ImageInputError('Malformed OCI/layer archive') from error


def _component_config(config, policy, owner):
    try:
        require(config['config']['Entrypoint'] == entrypoint(owner) and config['config']['Cmd'] == ['--help']
                and config['config']['User'] == '10001:10001', 'Component entrypoint/user changed')
        require(config['config']['Labels']['io.hub.production-qualified'] == 'false'
                and config['config']['Labels']['io.hub.native-helpers'] == 'absent'
                and config['config']['Labels']['org.opencontainers.image.revision'] == policy['components'][owner]['source_commit'],
                'Image qualification/source boundary changed')
    except (KeyError, TypeError) as error:
        raise ImageInputError('Malformed component config') from error


def _verified_component(path, policy, owner):
    require(owner in OWNERS, 'Unknown component owner')
    data = _read_oci(path)
    base_layers = policy['base']['rootfs_diff_ids']
    require(data['receipt']['rootfs_diff_ids'][:len(base_layers)] == base_layers, 'Pinned base filesystem changed')
    _component_config(data['config'], policy, owner)
    return data


def verify_oci(path, policy, owner):
    return _verified_component(path, policy, owner)['receipt']


def verify_base_export(path, base):
    data = _read_oci(path, base_export=True)
    receipt = data['receipt']
    require(receipt['manifest_digest'] == base['manifest_digest']
            and receipt['config_digest'] == base['config_digest']
            and receipt['rootfs_diff_ids'] == base['rootfs_diff_ids'], 'Local base export differs from pinned manifest/config/rootfs')
    return {**receipt, 'manifest_size': data['manifest_descriptor']['size']}


def _inspect_local(docker, reference, output, name):
    result = subprocess.run([docker, 'image', 'inspect', reference], capture_output=True, timeout=20)
    require(len(result.stdout)+len(result.stderr) <= MAX_JSON_BYTES, 'Oversized local image inspect')
    (output/(name+'.stdout')).write_bytes(result.stdout)
    (output/(name+'.stderr')).write_bytes(result.stderr)
    (output/(name+'.receipt.json')).write_bytes(canonical({'reference': reference, 'returncode': result.returncode,
        'stdout_sha256': sha(output/(name+'.stdout')), 'stderr_sha256': sha(output/(name+'.stderr'))})+b'\n')
    if result.returncode:
        require(result.returncode == 1, 'Local image inspect failed')
        return None
    try: inspected = json.loads(result.stdout)
    except (TypeError, ValueError) as error: raise ImageInputError('Malformed local image inspect') from error
    require(type(inspected) is list and len(inspected) == 1 and type(inspected[0]) is dict, 'Ambiguous local image identity')
    return inspected[0]


def _screen_local(inspected, manifest_digest, config_digest, diff_ids):
    require(inspected.get('Architecture') == 'arm64' and inspected.get('Os') == 'linux'
            and inspected.get('RootFS') == {'Type': 'layers', 'Layers': diff_ids}, 'Local image platform/rootfs mismatch')
    identity = inspected.get('Id'); descriptor = inspected.get('Descriptor')
    require(identity in (manifest_digest, config_digest), 'Local image identity is not an admitted digest')
    if identity == manifest_digest or descriptor is not None:
        require(type(descriptor) is dict and descriptor.get('digest') == manifest_digest
                and descriptor.get('mediaType') == 'application/vnd.oci.image.manifest.v1+json'
                and type(descriptor.get('size')) is int and 0 < descriptor['size'] <= MAX_JSON_BYTES,
                'Local manifest descriptor mismatch')
    return 'containerd-manifest-id' if identity == manifest_digest else 'classic-config-id'


def verify_preloaded_base(base, output, docker):
    inspected = _inspect_local(docker, base['reference'], output, 'base.inspect')
    require(inspected is not None, 'Pinned base not preloaded')
    mode = _screen_local(inspected, base['manifest_digest'], base['config_digest'], base['rootfs_diff_ids'])
    aliases = {prefix+'@'+base['manifest_digest'] for prefix in ('python', 'library/python', 'docker.io/library/python')}
    require(type(inspected.get('RepoDigests')) is list and all(type(item) is str for item in inspected['RepoDigests'])
            and aliases.intersection(inspected['RepoDigests']),
            'Local base repository digest mismatch')
    receipt = {'mode': mode, 'local_image_id': inspected['Id'], 'manifest_digest': base['manifest_digest'],
               'config_digest': base['config_digest'], 'rootfs_diff_ids': base['rootfs_diff_ids'],
               'raw_local_export_verified': False}
    if mode == 'containerd-manifest-id':
        path = output/'base.local-save.tar'
        command = [docker, 'image', 'save', '--platform', base['platform'], '--output', str(path), base['reference']]
        with (output/'base.local-save.log').open('xb') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        receipt.update(verify_base_export(path, base))
        require(inspected['Descriptor']['size'] == receipt['manifest_size'],
                'Local base manifest descriptor size mismatch')
        receipt['raw_local_export_verified'] = True
    (output/'base.verification.json').write_bytes(canonical(receipt)+b'\n')
    return receipt


def select_loaded_image(path, policy, owner, expected, output, docker):
    # Rebind the selector to exact validated raw archive bytes. Never select a
    # tag or treat inspect's summary as a config SHA or manifest substitute.
    data = _verified_component(path, policy, owner)
    actual = data['receipt']
    require(actual == expected, 'Loaded image receipt differs from validated archive')
    for position, reference in enumerate((actual['manifest_digest'], actual['config_digest'])):
        inspected = _inspect_local(docker, reference, output, owner+'.local-inspect-'+str(position))
        if inspected is None: continue
        mode = _screen_local(inspected, actual['manifest_digest'], actual['config_digest'], actual['rootfs_diff_ids'])
        raw_config = data['config']['config']; local_config = inspected.get('Config')
        require(type(local_config) is dict and all(local_config.get(key) == raw_config.get(key)
                for key in ('Entrypoint', 'Cmd', 'User', 'Labels', 'Env', 'WorkingDir')), 'Loaded image config summary contradicts raw archive')
        if inspected.get('Descriptor') is not None:
            require(inspected['Descriptor']['size'] == data['manifest_descriptor']['size'], 'Loaded image manifest size mismatch')
        result = {'mode': mode, 'local_image_id': inspected['Id'], 'archive_sha256': actual['archive_sha256'],
                  'manifest_digest': actual['manifest_digest'], 'config_digest': actual['config_digest'],
                  'rootfs_diff_ids': actual['rootfs_diff_ids'], 'raw_archive_reverified': True}
        (output/(owner+'.local-image.json')).write_bytes(canonical(result)+b'\n')
        return result
    raise ImageInputError('Validated component image is absent from local store')


def build_images(root, expected_sha, output, docker='docker'):
    manifest = validate_prepared(root, expected_sha)
    require(not output.exists(), 'Build output already exists')
    policy = manifest['policy']
    # Coordinate/preload this exact public base outside this command. No pull,
    # daemon startup, builder creation, registry login or push occurs here.
    output.mkdir(mode=0o700)
    base = verify_preloaded_base(policy['base'], output, docker)
    tooling = {}
    for name, command in [('engine', [docker, 'version', '--format', '{{json .Server}}']),
                          ('buildx', [docker, 'buildx', 'version'])]:
        result = subprocess.run(command, capture_output=True, check=True, timeout=20)
        require(len(result.stdout) < 65536, 'Oversized toolchain response')
        tooling[name] = result.stdout.decode().strip()
    executable = shutil.which(docker)
    require(executable is not None, 'Docker executable unavailable')
    tooling['docker_executable_sha256'] = sha(executable)
    (output/'toolchain.json').write_bytes(canonical(tooling)+b'\n')
    images = {}
    for owner in sorted(OWNERS):
        records = []
        for repetition in (1, 2):
            validate_prepared(root, expected_sha)
            path = output/(owner+'-'+str(repetition)+'.oci.tar')
            command = [docker, 'buildx', 'build', '--builder', 'default', '--platform', policy['base']['platform'],
                '--network=none', '--pull=false', '--provenance=false', '--sbom=false', '--build-arg', 'SOURCE_DATE_EPOCH=0',
                '--output', 'type=oci,dest='+str(path)+',compression=gzip,force-compression=true,rewrite-timestamp=true', str(root/owner)]
            with (output/(owner+'-'+str(repetition)+'.build.log')).open('wb') as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            records.append(verify_oci(path, policy, owner))
        require(records[0] == records[1], 'Repeated image exports differ; no publication')
        with (output/(owner+'.load.log')).open('wb') as log:
            subprocess.run([docker, 'load', '--input', str(output/(owner+'-1.oci.tar'))], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        selected = select_loaded_image(output/(owner+'-1.oci.tar'), policy, owner, records[0], output, docker)
        image_id = selected['local_image_id']
        container = [docker, 'run', '--rm', '--pull=never', '--network=none', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--tmpfs', '/data:uid=10001,gid=10001,mode=0700', '--tmpfs', '/tmp:mode=1777',
            '--entrypoint', '/opt/component/bin/python', image_id, '-I', '-B']
        result = subprocess.run(container+['/opt/image-tools/smoke.py', owner], capture_output=True, check=True, timeout=60)
        smoke = json.loads(result.stdout)
        require(smoke['component'] == owner and smoke['production_default_deny'] is True
                and smoke['external_sends'] == 0 and smoke['production_qualified'] is False, 'Image smoke rejected')
        (output/(owner+'.smoke.json')).write_bytes(canonical(smoke)+b'\n')
        installed = subprocess.run(container+['/opt/image-tools/inventory.py', '--verify-build', '/opt/evidence/build-inputs.json'],
                                   capture_output=True, check=True, timeout=60)
        require(len(installed.stdout) < 8*1024*1024, 'Oversized installed image inventory')
        inventory = json.loads(installed.stdout)
        (output/(owner+'.installed.json')).write_bytes(canonical(inventory)+b'\n')
        build_inputs = read_json(root/owner/'build-inputs.json')
        resources = {package['name']: {name: digest for name, digest in package['members'].items() if '/resources/' in name}
                     for package in build_inputs['packages'] if any('/resources/' in n for n in package['members'])}
        images[owner] = {**records[0], 'source_commit': policy['components'][owner]['source_commit'],
            'local_image_selection': selected,
            'reproduced_identically': True, 'smoke': smoke, 'resource_inventory': resources,
            'installed_inventory_sha256': sha(output/(owner+'.installed.json')),
            'build_input_sha256': sha(root/owner/'build-inputs.json')}
    report = {'schema': 'hub.component-image-artifacts.v1', 'context_manifest_sha256': expected_sha,
              'policy': policy, 'toolchain': tooling, 'base_verification': base, 'images': images,
              'publication': 'local artifacts only; never pushed', 'production_ready': False}
    (output/'image-manifest.json').write_bytes(canonical(report)+b'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify', 'build'))
    parser.add_argument('--policy', type=Path, default=HUB/'config/process-component-images.v1.json')
    parser.add_argument('--tuple', type=Path)
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--contexts', required=True, type=Path)
    parser.add_argument('--context-sha256')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            require(args.tuple and args.wheelhouse, 'Tuple and wheelhouse required')
            prepare_contexts(read_json(args.policy), args.tuple, args.wheelhouse, args.contexts)
            result = {'context_manifest_sha256': sha(args.contexts/'contexts.json'), 'status': 'prepared'}
        else:
            require(args.context_sha256, 'Reviewed context digest required')
            if args.action == 'verify':
                validate_prepared(args.contexts, args.context_sha256); result = {'status': 'verified'}
            else:
                require(args.output, 'Create-only build output required')
                build_images(args.contexts, args.context_sha256, args.output); result = {'status': 'built-and-smoked'}
        print(json.dumps({**result, 'production_ready': False}, sort_keys=True)); return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'production_ready': False})); return 1


if __name__ == '__main__':
    raise SystemExit(main())
