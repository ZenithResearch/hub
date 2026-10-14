"""Prepublication image input tests; real container smoke is a separate receipt."""
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
from unittest.mock import patch
import zipfile

import pytest

from scripts.process_component_images import (
    ImageInputError, build_images, canonical, entrypoint, prepare_contexts, validate_prepared,
    verify_oci, wheel_inventory, HOST_TOOLS,
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def wheel(directory, name, version='0.1.0', *, requires=(), platform='any', unsafe=None, binary=None):
    stem = name.replace('-', '_')
    filename = f'{stem}-{version}-py3-none-{platform}.whl'
    info = f'{stem}-{version}.dist-info'
    entries = {f'{stem}/__init__.py': b'', f'{info}/METADATA':
               ('Metadata-Version: 2.1\nName: '+name+'\nVersion: '+version+'\nRequires-Python: >=3.12,<3.13\n'+
                ''.join('Requires-Dist: '+r+'\n' for r in requires)).encode(),
               f'{info}/WHEEL': ('Wheel-Version: 1.0\nTag: py3-none-'+platform+'\n').encode()}
    if name in ('process-runtime', 'frank-executor'):
        entries['schemas/runtime.json'] = b'{"schema":"runtime-fixture.v1"}'
    if unsafe: entries[unsafe] = b'unsafe'
    if binary: entries['native-helper'] = binary
    records = [f'{p},sha256={base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")},{len(raw)}\n'
               for p, raw in entries.items()]
    entries[f'{info}/RECORD'] = (''.join(records)+f'{info}/RECORD,,\n').encode()
    path = directory / filename
    with zipfile.ZipFile(path, 'w') as archive:
        for p, raw in entries.items(): archive.writestr(p, raw)
    return {'filename': filename, 'name': name, 'version': version, 'sha256': sha(path.read_bytes())}


@pytest.fixture
def inputs(tmp_path):
    wheelhouse = tmp_path / 'wheels'; wheelhouse.mkdir()
    wheels = [wheel(wheelhouse, 'process-runtime', requires=('jsonschema>=4.25,<5',)),
              wheel(wheelhouse, 'service-management'),
              wheel(wheelhouse, 'frank-executor', requires=('process-runtime==0.1.0',)),
              wheel(wheelhouse, 'jsonschema', '4.26.0')]
    policy = json.loads((Path(__file__).resolve().parents[1] / 'config/process-component-images.v1.json').read_text())
    policy['wheels'] = wheels
    policy['contracts'] = {key: {'package': name, 'member': 'schemas/runtime.json',
                                'sha256': sha(b'{"schema":"runtime-fixture.v1"}')}
                           for key, name in [('runtime-sdk', 'process-runtime'), ('frank-runtime', 'frank-executor')]}
    components = {}
    for owner, name in [('process-runtime', 'process-runtime'), ('service-management', 'service-management'), ('frank', 'frank-executor')]:
        item = next(w for w in wheels if w['name'] == name)
        policy['components'][owner].update(source_commit='a'*40, wheel_sha256=item['sha256'])
        components[owner] = {'package': name, 'version': '0.1.0', 'wheel': '/not-used/'+item['filename'], 'wheel_sha256': item['sha256']}
    artifact = {'schema': 'hub.process-artifact-tuple.v1', 'components': components,
                'dependencies': {w['name']: {'version': w['version'], 'files': {}} for w in wheels},
                'production_ready': False}
    policy['tuple_manifest_sha256'] = sha(canonical(artifact))
    tuple_path = tmp_path / 'tuple.json'; tuple_path.write_bytes(canonical(artifact))
    return policy, tuple_path, wheelhouse, tmp_path


def prepare(inputs, name='prepared'):
    policy, artifact, wheels, directory = inputs
    return prepare_contexts(policy, artifact, wheels, directory/name)


def test_prepared_contexts_reproduce_and_separate_component_roots(inputs):
    first = prepare(inputs)
    second = prepare(inputs, 'second')
    assert first == second
    root = inputs[3] / 'prepared'
    validate_prepared(root, sha((root/'contexts.json').read_bytes()))
    for owner in ('process-runtime', 'service-management', 'frank'):
        manifest = json.loads((root/owner/'build-inputs.json').read_text())
        assert manifest['component'] == owner and manifest['production_ready'] is False
        assert manifest['native_helpers'] == []
        assert manifest['host_tools'] == HOST_TOOLS
        component = manifest['source']
        admitted = next(p for p in manifest['packages'] if p['name'] == component['package'])
        assert admitted['sha256'] == component['wheel_sha256']
        assert 'RUN --network=none' in (root/owner/'Dockerfile').read_text()
        assert '--require-hashes' in (root/owner/'Dockerfile').read_text()
    runtime = (root/'process-runtime/requirements.lock').read_text()
    assert 'frank-executor' not in runtime and 'service-management' not in runtime


@pytest.mark.parametrize('change', ['missing', 'tampered', 'unlocked-extra'])
def test_missing_tampered_and_extra_wheels_deny_without_context(inputs, change):
    policy, _, wheels, directory = inputs
    path = wheels/policy['wheels'][0]['filename']
    if change == 'missing': path.unlink()
    elif change == 'tampered': path.write_bytes(path.read_bytes()+b'x')
    else: (wheels/'extra.whl').write_bytes(b'not admitted')
    with pytest.raises(ImageInputError): prepare(inputs)
    assert not (directory/'prepared').exists()


def test_tuple_mutation_denies_even_same_component_versions(inputs):
    path = inputs[1]; artifact = json.loads(path.read_bytes())
    artifact['components']['frank']['wheel_sha256'] = '0'*64
    path.write_bytes(canonical(artifact))
    with pytest.raises(ImageInputError): prepare(inputs)


def test_admitted_component_cannot_disagree_with_source_and_tuple_hash(inputs):
    policy, _, wheels, directory = inputs
    row = next(w for w in policy['wheels'] if w['name'] == 'process-runtime')
    original = row['sha256']
    replacement = wheel(wheels, 'process-runtime', requires=('jsonschema>=4.25,<5',), binary=b'changed-valid-payload')
    assert replacement['sha256'] != original
    row['sha256'] = replacement['sha256']
    with pytest.raises(ImageInputError): prepare(inputs)
    assert not (directory/'prepared').exists()


@pytest.mark.parametrize('change', ['wrong-python', 'mutable-base', 'native-helper', 'unknown-component'])
def test_policy_cannot_weaken_platform_or_native_boundary(inputs, change):
    policy = inputs[0]
    if change == 'wrong-python': policy['base']['python_version'] = '3.13.0'
    elif change == 'mutable-base': policy['base']['reference'] = 'python:latest'
    elif change == 'native-helper': policy['native_helpers'] = ['darwin-helper']
    else: policy['components']['other'] = {}
    with pytest.raises(ImageInputError): prepare(inputs)


@pytest.mark.parametrize('platform', ['macosx_11_0_arm64', 'manylinux_2_28_x86_64', 'win_amd64'])
def test_wrong_platform_wheel_is_rejected_before_build(tmp_path, platform):
    item = wheel(tmp_path, 'example', platform=platform)
    with pytest.raises(ImageInputError): wheel_inventory(tmp_path/item['filename'], item, 'linux/arm64')


def test_zip_path_traversal_denies_even_reviewed_file_digest(tmp_path):
    item = wheel(tmp_path, 'example', unsafe='../private-key')
    with pytest.raises(ImageInputError): wheel_inventory(tmp_path/item['filename'], item, 'linux/arm64')


def test_darwin_helper_inside_any_tag_wheel_is_rejected(tmp_path):
    item = wheel(tmp_path, 'example', binary=b'\xcf\xfa\xed\xfe' + b'fixture')
    with pytest.raises(ImageInputError, match='Darwin'): wheel_inventory(tmp_path/item['filename'], item, 'linux/arm64')


def test_missing_and_incompatible_transitive_dependencies_deny(inputs):
    policy, artifact, wheels, directory = inputs
    old = next(w for w in policy['wheels'] if w['name']=='jsonschema')
    policy['wheels'].remove(old); (wheels/old['filename']).unlink()
    with pytest.raises(ImageInputError): prepare(inputs)


def test_contract_hash_mismatch_denies_before_build(inputs):
    inputs[0]['contracts']['frank-runtime']['sha256'] = '0'*64
    with pytest.raises(ImageInputError): prepare(inputs)


def test_incompatible_metadata_dependency_denies_before_build(inputs):
    policy, artifact, wheels, _ = inputs
    old = next(w for w in policy['wheels'] if w['name']=='jsonschema')
    (wheels/old['filename']).unlink()
    new = wheel(wheels, 'jsonschema', '3.0.0'); policy['wheels'].remove(old); policy['wheels'].append(new)
    value = json.loads(artifact.read_bytes()); value['dependencies']['jsonschema']['version'] = '3.0.0'
    artifact.write_bytes(canonical(value)); policy['tuple_manifest_sha256'] = sha(artifact.read_bytes())
    with pytest.raises(ImageInputError, match='Incompatible transitive'): prepare(inputs)


@pytest.mark.parametrize('target', ['Dockerfile', 'requirements.lock', 'build-inputs.json', 'inventory.py'])
def test_changed_context_denies_prepublication(inputs, target):
    prepare(inputs); root=inputs[3]/'prepared'; digest=sha((root/'contexts.json').read_bytes())
    path=root/'frank'/target; path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ImageInputError): validate_prepared(root,digest)


def test_added_private_config_denies_prepublication(inputs):
    prepare(inputs); root=inputs[3]/'prepared'; digest=sha((root/'contexts.json').read_bytes())
    (root/'frank'/'.env').write_text('PRIVATE=sentinel')
    with pytest.raises(ImageInputError): validate_prepared(root,digest)


def test_prepared_root_requires_reviewed_digest_and_no_overwrite(inputs):
    prepare(inputs); root=inputs[3]/'prepared'
    with pytest.raises(ImageInputError): validate_prepared(root,'0'*64)
    with pytest.raises(ImageInputError): prepare(inputs)


def test_context_drift_is_rejected_before_any_docker_action(inputs):
    prepare(inputs); root=inputs[3]/'prepared'; digest=sha((root/'contexts.json').read_bytes())
    (root/'frank/Dockerfile').write_text('FROM unreviewed:latest')
    with patch('scripts.process_component_images.subprocess.run') as docker:
        with pytest.raises(ImageInputError): build_images(root,digest,inputs[3]/'images')
    docker.assert_not_called()
    assert not (inputs[3]/'images').exists()


def layer_tar(number):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        payload = f'fixture {number}'.encode()
        item = tarfile.TarInfo(f'layer-{number}.txt'); item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload))
    return output.getvalue()


def image_archive(path, policy, *, mutate=None, corrupt=False, false_diff_ids=False,
                  layer_bytes=None, media='application/vnd.oci.image.layer.v1.tar+gzip', encoded=None,
                  descriptor_mutate=None, archive_extra=None):
    layers = [layer_tar(n) for n in range(4)] if layer_bytes is None else layer_bytes
    actual_diff_ids = ['sha256:'+sha(raw) for raw in layers]
    claims = ['sha256:'+str(n)*64 for n in range(4)] if false_diff_ids else actual_diff_ids
    policy['base']['rootfs_diff_ids'] = claims
    owner='frank'; config={'architecture':'arm64','os':'linux',
        'rootfs':{'type':'layers','diff_ids':claims},
        'config':{'Entrypoint':entrypoint(owner),'Cmd':['--help'],'User':'10001:10001',
                  'Labels':{'io.hub.production-qualified':'false','io.hub.native-helpers':'absent',
                            'org.opencontainers.image.revision':policy['components'][owner]['source_commit']}}}
    if mutate: mutate(config)
    files={}
    def blob(raw, media):
        digest=sha(raw);files['blobs/sha256/'+digest]=raw
        return {'digest':'sha256:'+digest,'size':len(raw),'mediaType':media}
    cfg=blob(canonical(config),'application/vnd.oci.image.config.v1+json')
    descriptors = [blob(encoded if encoded is not None else gzip.compress(raw,mtime=0) if media.endswith('+gzip') else raw,media)
                   for raw in layers]
    if descriptor_mutate: descriptor_mutate(descriptors)
    manifest=blob(canonical({'schemaVersion':2,'config':cfg,'layers':descriptors}), 'application/vnd.oci.image.manifest.v1+json')
    files['index.json']=canonical({'schemaVersion':2,'manifests':[manifest]})
    files['oci-layout']=canonical({'imageLayoutVersion':'1.0.0'})
    if corrupt: files['blobs/sha256/'+cfg['digest'].split(':')[1]]=b'changed'
    if archive_extra: files.update(archive_extra)
    with tarfile.open(path,'w') as archive:
        for name,raw in files.items():
            entry=tarfile.TarInfo(name);entry.size=len(raw);archive.addfile(entry,io.BytesIO(raw))


def test_oci_artifact_digest_and_owner_entrypoint_validation(inputs):
    path=inputs[3]/'image.tar';image_archive(path,inputs[0])
    result=verify_oci(path,inputs[0],'frank')
    assert result['archive_sha256']==sha(path.read_bytes())
    assert result['rootfs_diff_ids'] == inputs[0]['base']['rootfs_diff_ids']
    assert result['uncompressed_layer_bytes'] == sum(len(layer_tar(n)) for n in range(4))


@pytest.mark.parametrize('change', ['architecture','entrypoint','user','qualification','source','base'])
def test_wrong_oci_image_is_rejected_before_publication(inputs,change):
    def mutate(config):
        if change=='architecture':config['architecture']='amd64'
        elif change=='entrypoint':config['config']['Entrypoint']=['sh']
        elif change=='user':config['config']['User']='root'
        elif change=='qualification':config['config']['Labels']['io.hub.production-qualified']='true'
        elif change=='source':config['config']['Labels']['org.opencontainers.image.revision']='0'*40
        else:config['rootfs']['diff_ids']=[]
    path=inputs[3]/'image.tar';image_archive(path,inputs[0],mutate=mutate)
    with pytest.raises(ImageInputError):verify_oci(path,inputs[0],'frank')


def test_tampered_oci_blob_rejected(inputs):
    path=inputs[3]/'image.tar';image_archive(path,inputs[0],corrupt=True)
    with pytest.raises(ImageInputError):verify_oci(path,inputs[0],'frank')


def test_valid_compressed_descriptors_cannot_lie_about_diff_ids(inputs):
    path=inputs[3]/'false-base.tar';image_archive(path,inputs[0],false_diff_ids=True)
    with pytest.raises(ImageInputError): verify_oci(path,inputs[0],'frank')


def test_valid_uncompressed_oci_layers(inputs):
    path=inputs[3]/'plain.tar';image_archive(path,inputs[0],media='application/vnd.oci.image.layer.v1.tar')
    assert verify_oci(path,inputs[0],'frank')['rootfs_diff_ids'] == inputs[0]['base']['rootfs_diff_ids']


@pytest.mark.parametrize('kind', ['order', 'count', 'rootfs-type', 'unsupported', 'bad-gzip', 'bad-tar', 'trailing-payload'])
def test_layer_structure_and_order_are_verified(inputs,kind):
    kwargs = {}
    if kind == 'order': kwargs['descriptor_mutate'] = lambda layers: layers.reverse()
    elif kind == 'count': kwargs['mutate'] = lambda cfg: cfg['rootfs']['diff_ids'].pop()
    elif kind == 'rootfs-type': kwargs['mutate'] = lambda cfg: cfg['rootfs'].update(type='other')
    elif kind == 'unsupported': kwargs['media'] = 'application/vnd.oci.image.layer.v1.tar+zstd'
    elif kind == 'bad-gzip': kwargs['encoded'] = b'invalid gzip with valid compressed SHA'
    elif kind == 'bad-tar': kwargs['layer_bytes'] = [b'not a tar']
    else: kwargs['layer_bytes'] = [layer_tar(0)+b'hidden nonzero trailer'+b'\0'*(512-22)]
    path=inputs[3]/'bad-structure.tar';image_archive(path,inputs[0],**kwargs)
    with pytest.raises(ImageInputError): verify_oci(path,inputs[0],'frank')


def tar_with_member(name, *, link=None, kind=None):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as archive:
        item = tarfile.TarInfo(name)
        if link is not None: item.type=tarfile.SYMTYPE; item.linkname=link
        if kind is not None: item.type=kind
        archive.addfile(item)
    return raw.getvalue()


@pytest.mark.parametrize('raw', [tar_with_member('../escape'), tar_with_member('/absolute'),
    tar_with_member('a/link',link='../../escape'), tar_with_member('device',kind=tarfile.CHRTYPE)])
def test_layer_paths_links_and_special_members_deny(inputs,raw):
    path=inputs[3]/'unsafe-layer.tar';image_archive(path,inputs[0],layer_bytes=[raw])
    with pytest.raises(ImageInputError): verify_oci(path,inputs[0],'frank')


@pytest.mark.parametrize('link', ['../lib/python3.12', '/usr/lib/python3.12'])
def test_safe_container_root_symlinks_are_accepted_without_extraction(inputs,link):
    path=inputs[3]/'safe-link.tar';image_archive(path,inputs[0],layer_bytes=[tar_with_member('usr/bin/python',link=link)])
    assert verify_oci(path,inputs[0],'frank')['rootfs_diff_ids'] == inputs[0]['base']['rootfs_diff_ids']


@pytest.mark.parametrize('constant,bound', [('MAX_OCI_BYTES',100), ('MAX_OCI_MEMBERS',2),
    ('MAX_JSON_BYTES',20), ('MAX_LAYERS',2), ('MAX_COMPRESSED_LAYER',30),
    ('MAX_LAYER_BYTES',5000), ('MAX_TOTAL_LAYER_BYTES',30000), ('MAX_LAYER_MEMBERS',0), ('MAX_TAR_HEADERS',0)])
def test_oci_limits_deny_before_publication(inputs,monkeypatch,constant,bound):
    path=inputs[3]/'bounded.tar';image_archive(path,inputs[0])
    monkeypatch.setattr('scripts.process_component_images.'+constant,bound)
    with pytest.raises(ImageInputError): verify_oci(path,inputs[0],'frank')


def test_extended_tar_metadata_allocation_is_bounded(inputs):
    path=inputs[3]/'extended.tar'
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w',format=tarfile.PAX_FORMAT) as archive:
        item=tarfile.TarInfo('a');item.pax_headers={'comment':'x'*70000};archive.addfile(item)
    image_archive(path,inputs[0],layer_bytes=[raw.getvalue()])
    with pytest.raises(ImageInputError,match='extended metadata'):verify_oci(path,inputs[0],'frank')


def test_unreferenced_oci_payload_denies(inputs):
    path=inputs[3]/'extra.tar';image_archive(path,inputs[0],archive_extra={'unadmitted-data':b'fixture'})
    with pytest.raises(ImageInputError,match='Unreferenced'):verify_oci(path,inputs[0],'frank')


def test_empty_but_valid_layer_tar_is_supported(inputs):
    path=inputs[3]/'empty-layer.tar'
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w'): pass
    image_archive(path,inputs[0],layer_bytes=[raw.getvalue()])
    assert verify_oci(path,inputs[0],'frank')['rootfs_diff_ids'] == inputs[0]['base']['rootfs_diff_ids']


def test_nonzero_hidden_oci_trailer_is_rejected(inputs):
    path=inputs[3]/'outer-trailer.tar';image_archive(path,inputs[0])
    with path.open('ab') as output: output.write(b'hidden'+b'\0'*506)
    with pytest.raises(ImageInputError,match='trailing OCI'): verify_oci(path,inputs[0],'frank')
